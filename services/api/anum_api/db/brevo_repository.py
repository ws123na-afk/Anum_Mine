"""PostgreSQL store for Brevo email campaigns (migration ``0074_brevo_campaigns``).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every
query also filters on the scope explicitly, so a guessed id from another workspace reads
as missing even before the row policy applies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.brevo.models import BrevoConnection, CampaignDraft, DraftStatus
from anum_api.schemas import TenantContext

from .models import Base
from .scope import require_workspace

TS = DateTime(timezone=True)


class BrevoConnectionRecord(Base):
    __tablename__ = "brevo_connections"
    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    api_key_ciphertext: Mapped[str] = mapped_column(Text)
    key_last4: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(20))
    account_email: Mapped[str] = mapped_column(String(254))
    account_name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(60))
    last_checked_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class BrevoDraftRecord(Base):
    __tablename__ = "brevo_campaign_drafts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    workspace_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(200))
    preview_text: Mapped[str] = mapped_column(String(200))
    sender_id: Mapped[int] = mapped_column(BigInteger)
    sender_name: Mapped[str] = mapped_column(String(120))
    sender_email: Mapped[str] = mapped_column(String(254))
    html: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    list_ids: Mapped[list[Any]] = mapped_column(JSONB)
    list_names: Mapped[list[Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    scheduled_at: Mapped[datetime | None] = mapped_column(TS)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    approval_task_id: Mapped[str | None] = mapped_column(String(80))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(160))
    brevo_campaign_id: Mapped[int | None] = mapped_column(BigInteger)
    send_attempts: Mapped[int] = mapped_column(Integer)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    test_sent_at: Mapped[datetime | None] = mapped_column(TS)
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


_CONNECTION_FIELDS = [column.key for column in BrevoConnectionRecord.__mapper__.column_attrs]
_DRAFT_FIELDS = [column.key for column in BrevoDraftRecord.__mapper__.column_attrs]


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlAlchemyBrevoStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    # -- connection -------------------------------------------------------------------------

    def _connection(self, *, for_update: bool) -> BrevoConnectionRecord | None:
        statement = select(BrevoConnectionRecord).where(
            BrevoConnectionRecord.tenant_id == self.context.tenant_id,
            BrevoConnectionRecord.workspace_id == self.context.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_connection(self, *, for_update: bool = False) -> BrevoConnection | None:
        found = self._connection(for_update=for_update)
        return BrevoConnection.model_validate({name: getattr(found, name) for name in _CONNECTION_FIELDS}) if found is not None else None

    def save_connection(self, connection: BrevoConnection) -> BrevoConnection:
        if (connection.tenant_id, connection.workspace_id) != (self.context.tenant_id, self.context.workspace_id):
            raise PermissionError("record outside the caller's workspace")
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = self._connection(for_update=True)
        if record is None:
            record = BrevoConnectionRecord()
            self.session.add(record)
        data = connection.model_dump()
        for name in _CONNECTION_FIELDS:
            value = data[name]
            setattr(record, name, value.value if hasattr(value, "value") else value)
        self.session.flush()
        return connection

    def delete_connection(self) -> bool:
        record = self._connection(for_update=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    # -- drafts -----------------------------------------------------------------------------

    def _scope(self) -> tuple[Any, Any]:
        return (BrevoDraftRecord.tenant_id == self.context.tenant_id, BrevoDraftRecord.workspace_id == self.context.workspace_id)

    def _draft(self, draft_id: str, *, for_update: bool) -> BrevoDraftRecord | None:
        statement = select(BrevoDraftRecord).where(*self._scope(), BrevoDraftRecord.id == draft_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    @staticmethod
    def _model(record: BrevoDraftRecord) -> CampaignDraft:
        return CampaignDraft.model_validate({name: getattr(record, name) for name in _DRAFT_FIELDS})

    @staticmethod
    def _copy(draft: CampaignDraft, record: BrevoDraftRecord) -> None:
        data = draft.model_dump(mode="json")
        native = draft.model_dump()
        for name in _DRAFT_FIELDS:
            value = native[name]
            if isinstance(value, (list, dict)) or value is None and name == "last_error":
                setattr(record, name, data[name])
            else:
                setattr(record, name, value.value if hasattr(value, "value") else value)

    def get_draft(self, draft_id: str, *, for_update: bool = False) -> CampaignDraft | None:
        found = self._draft(draft_id, for_update=for_update)
        return self._model(found) if found is not None else None

    def add_draft(self, draft: CampaignDraft) -> CampaignDraft:
        if (draft.tenant_id, draft.workspace_id) != (self.context.tenant_id, self.context.workspace_id):
            raise PermissionError("record outside the caller's workspace")
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = BrevoDraftRecord()
        self._copy(draft, record)
        self.session.add(record)
        self.session.flush()
        return draft

    def save_draft(self, draft: CampaignDraft) -> CampaignDraft:
        record = self._draft(draft.id, for_update=True)
        if record is None or (draft.tenant_id, draft.workspace_id) != (self.context.tenant_id, self.context.workspace_id):
            raise KeyError(draft.id)
        self._copy(draft, record)
        self.session.flush()
        return draft

    def delete_draft(self, draft_id: str) -> bool:
        record = self._draft(draft_id, for_update=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_drafts(self, *, text: str | None, status: DraftStatus | None, limit: int, offset: int) -> tuple[list[CampaignDraft], int]:
        statement = select(BrevoDraftRecord).where(*self._scope())
        if status is not None:
            statement = statement.where(BrevoDraftRecord.status == status.value)
        if text and text.strip():
            pattern = _like(text.strip())
            statement = statement.where(or_(BrevoDraftRecord.name.ilike(pattern, escape="\\"), BrevoDraftRecord.subject.ilike(pattern, escape="\\")))
        total = int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        rows = self.session.scalars(
            statement.order_by(BrevoDraftRecord.updated_at.desc(), BrevoDraftRecord.id.desc()).offset(offset).limit(limit)
        ).all()
        return [self._model(row) for row in rows], total
