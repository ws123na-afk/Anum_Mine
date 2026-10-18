"""PostgreSQL store of AI labels on mail (migration ``0066_mail_labels``, docs/email.md#ai-on-email).

The session carries the tenant, workspace and user RLS context (``open_label_store`` opens it
with ``user_scoped=True``); the table has a restrictive owner policy and every query here
repeats the scope and the owner. Saving is an upsert on (person, mailbox, folder, UID).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.mailbox.labels import MailLabel
from anum_api.schemas import TenantContext, utc_now

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class MailLabelRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_labels"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), ForeignKey("mail_accounts.id", ondelete="CASCADE", name="fk_mail_labels_account"), nullable=False)
    folder: Mapped[str] = mapped_column(String(500), nullable=False)
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("mail_labels"),
        UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "account_id", "folder", "uid", name="uq_mail_labels_message"),
        CheckConstraint("uid > 0", name="ck_mail_labels_uid"),
        Index("ix_mail_labels_account", "account_id"),
    )


def _label(row: MailLabelRecord) -> MailLabel:
    return MailLabel(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, account_id=row.account_id,
        folder=row.folder, uid=row.uid, message_id=row.message_id, category=row.category, priority=row.priority,  # type: ignore[arg-type]
        reason=row.reason, source=row.source, model=row.model, created_at=row.created_at, updated_at=row.updated_at,  # type: ignore[arg-type]
    )


class SqlAlchemyLabelStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.tenant_id = context.tenant_id
        self.workspace_id = context.workspace_id
        self.owner = context.user_id

    def _scope(self):  # type: ignore[no-untyped-def]
        return (MailLabelRecord.tenant_id == self.tenant_id, MailLabelRecord.workspace_id == self.workspace_id, MailLabelRecord.owner_user_id == self.owner)

    def labels_for(self, account_id: str, folder: str, uids: list[int]) -> dict[int, MailLabel]:
        if not uids:
            return {}
        rows = self.session.scalars(
            select(MailLabelRecord).where(*self._scope(), MailLabelRecord.account_id == account_id, MailLabelRecord.folder == folder, MailLabelRecord.uid.in_(uids))
        )
        return {row.uid: _label(row) for row in rows}

    def save_label(self, label: MailLabel) -> MailLabel:
        if (label.tenant_id, label.workspace_id, label.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        values = label.model_dump()
        changed = {key: values[key] for key in ("message_id", "category", "priority", "reason", "source", "model")}
        statement = insert(MailLabelRecord).values(**values).on_conflict_do_update(
            constraint="uq_mail_labels_message", set_={**changed, "updated_at": utc_now()},
        ).returning(MailLabelRecord.id, MailLabelRecord.created_at)
        stored_id, created_at = self.session.execute(statement).one()
        return label.model_copy(update={"id": stored_id, "created_at": created_at})

    def list_all(self) -> list[MailLabel]:
        rows = self.session.scalars(select(MailLabelRecord).where(*self._scope()).order_by(MailLabelRecord.created_at, MailLabelRecord.id))
        return [_label(row) for row in rows]

    def delete_all(self) -> int:
        result = self.session.execute(delete(MailLabelRecord).where(*self._scope()))
        return int(getattr(result, "rowcount", 0) or 0)

    def forget_account(self, account_id: str) -> None:
        """The foreign key deletes a mailbox's labels; nothing to do here."""
