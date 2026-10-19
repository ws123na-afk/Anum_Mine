"""PostgreSQL store of calendar sync connections (migration 0084_calendar_sync, docs/calendar.md#sync).

Both tables are scoped to a tenant and workspace under forced RLS; the session already
carries that context (``open_scoped_store``) and every query filters on it as well. Tokens
are stored only as Fernet ciphertext (``ANUM_SECRETS_KEY``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, String, Text, delete, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.calendar_providers import CalendarProvider
from anum_api.calendar_sync import CalendarConnection, CalendarOAuthPending
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class CalendarConnectionRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "calendar_connections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    connected_by: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sync_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("calendar_connections"),)


class CalendarOAuthRequestRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "calendar_oauth_requests"

    nonce: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    code_verifier_enc: Mapped[str | None] = mapped_column(Text)
    code_enc: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("calendar_oauth_requests"),)


_CONNECTION_FIELDS = (
    "provider", "email", "display_name", "connected_by", "status", "scopes", "access_token_enc", "refresh_token_enc",
    "access_expires_at", "sync_cursor", "last_sync_at", "last_sync_status", "last_error", "last_summary",
    "sync_lease_until", "created_at", "updated_at",
)
_PENDING_FIELDS = ("user_id", "provider", "code_verifier_enc", "code_enc", "error", "created_at", "expires_at")


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _copy(source: Any, record: Any, fields: tuple[str, ...]) -> None:
    for name in fields:
        value = getattr(source, name)
        setattr(record, name, list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else _plain(value))


def _values(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(record, name) for name in fields}


def _connection(record: CalendarConnectionRecord) -> CalendarConnection:
    return CalendarConnection(id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _CONNECTION_FIELDS))


def _pending(record: CalendarOAuthRequestRecord) -> CalendarOAuthPending:
    return CalendarOAuthPending(nonce=record.nonce, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _PENDING_FIELDS))


class SqlAlchemyCalendarSyncStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, model: Any) -> tuple[Any, Any]:
        return (model.tenant_id == self.context.tenant_id, model.workspace_id == self.context.workspace_id)

    def _record(self, connection_id: str, *, lock: bool = False) -> CalendarConnectionRecord | None:
        statement = select(CalendarConnectionRecord).where(*self._scope(CalendarConnectionRecord), CalendarConnectionRecord.id == connection_id)
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def list_connections(self) -> list[CalendarConnection]:
        records = self.session.scalars(
            select(CalendarConnectionRecord)
            .where(*self._scope(CalendarConnectionRecord))
            .order_by(CalendarConnectionRecord.created_at, CalendarConnectionRecord.id)
        ).all()
        return [_connection(r) for r in records]

    def get_connection(self, connection_id: str) -> CalendarConnection | None:
        record = self._record(connection_id)
        return _connection(record) if record is not None else None

    def find_connection(self, provider: CalendarProvider, email: str) -> CalendarConnection | None:
        record = self.session.scalar(
            select(CalendarConnectionRecord).where(
                *self._scope(CalendarConnectionRecord),
                CalendarConnectionRecord.provider == provider.value,
                CalendarConnectionRecord.email == email,
            )
        )
        return _connection(record) if record is not None else None

    def add_connection(self, connection: CalendarConnection) -> CalendarConnection:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = CalendarConnectionRecord(id=connection.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(connection, record, _CONNECTION_FIELDS)
        self.session.add(record)
        self.session.flush()
        return connection

    def save_connection(self, connection: CalendarConnection) -> CalendarConnection:
        record = self._record(connection.id, lock=True)
        if record is None:
            raise KeyError(connection.id)
        _copy(connection, record, _CONNECTION_FIELDS)
        self.session.flush()
        return connection

    def delete_connection(self, connection_id: str) -> None:
        self.session.execute(
            delete(CalendarConnectionRecord).where(*self._scope(CalendarConnectionRecord), CalendarConnectionRecord.id == connection_id)
        )
        self.session.flush()

    def claim_sync(self, connection_id: str, now: datetime, until: datetime) -> bool:
        c = CalendarConnectionRecord
        claimed = self.session.execute(
            update(c)
            .where(*self._scope(c), c.id == connection_id, or_(c.sync_lease_until.is_(None), c.sync_lease_until <= now))
            .values(sync_lease_until=until)
        )
        return bool(getattr(claimed, "rowcount", 0))

    def add_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = CalendarOAuthRequestRecord(nonce=pending.nonce, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(pending, record, _PENDING_FIELDS)
        self.session.add(record)
        self.session.flush()
        return pending

    def get_pending(self, nonce: str) -> CalendarOAuthPending | None:
        record = self.session.scalar(
            select(CalendarOAuthRequestRecord).where(*self._scope(CalendarOAuthRequestRecord), CalendarOAuthRequestRecord.nonce == nonce)
        )
        return _pending(record) if record is not None else None

    def save_pending(self, pending: CalendarOAuthPending) -> CalendarOAuthPending:
        record = self.session.scalar(
            select(CalendarOAuthRequestRecord)
            .where(*self._scope(CalendarOAuthRequestRecord), CalendarOAuthRequestRecord.nonce == pending.nonce)
            .with_for_update()
        )
        if record is None:
            raise KeyError(pending.nonce)
        _copy(pending, record, _PENDING_FIELDS)
        self.session.flush()
        return pending

    def delete_pending(self, nonce: str) -> None:
        self.session.execute(
            delete(CalendarOAuthRequestRecord).where(*self._scope(CalendarOAuthRequestRecord), CalendarOAuthRequestRecord.nonce == nonce)
        )
        self.session.flush()
