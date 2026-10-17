"""PostgreSQL store of the Email Center (migration ``0060_email_center``, docs/email.md#email-center).

The session carries the tenant, workspace and user RLS context (``open_mail_store`` opens it
with ``user_scoped=True``); both tables also have a restrictive owner policy, and every query
here repeats the scope and the owner. The mailbox password is stored only as Fernet ciphertext.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.mailbox.store import AccountStatus, DraftStatus, MailAccount, MailDraft
from anum_api.schemas import TenantContext, utc_now

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class MailAccountRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_accounts"),)


class MailDraftRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_drafts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    to_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    bcc_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    references_header: Mapped[str | None] = mapped_column(Text)
    source_folder: Mapped[str | None] = mapped_column(String(500))
    source_uid: Mapped[int | None] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(500), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    saved_to_sent: Mapped[bool | None] = mapped_column(Boolean)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_drafts"),)


def _account(row: MailAccountRecord) -> MailAccount:
    return MailAccount(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
        address=row.address, display_name=row.display_name, password_enc=row.password_enc,
        status=AccountStatus(row.status), last_error=row.last_error, last_checked_at=row.last_checked_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _draft(row: MailDraftRecord) -> MailDraft:
    return MailDraft(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
        account_id=row.account_id, kind=row.kind,  # type: ignore[arg-type]
        to=list(row.to_addresses or []), cc=list(row.cc_addresses or []), bcc=list(row.bcc_addresses or []),
        subject=row.subject, body=row.body, in_reply_to=row.in_reply_to, references=row.references_header,
        source_folder=row.source_folder, source_uid=row.source_uid, message_id=row.message_id,
        idempotency_key=row.idempotency_key, status=DraftStatus(row.status), approval_id=row.approval_id,
        task_id=row.task_id, error=row.error, saved_to_sent=row.saved_to_sent, sent_at=row.sent_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


class SqlAlchemyMailStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.tenant_id = context.tenant_id
        self.workspace_id = context.workspace_id
        self.owner = context.user_id

    def _scope(self, model: type[MailAccountRecord] | type[MailDraftRecord]):  # type: ignore[no-untyped-def]
        return (model.tenant_id == self.tenant_id, model.workspace_id == self.workspace_id, model.owner_user_id == self.owner)

    def list_accounts(self) -> list[MailAccount]:
        rows = self.session.scalars(select(MailAccountRecord).where(*self._scope(MailAccountRecord)).order_by(MailAccountRecord.created_at))
        return [_account(row) for row in rows]

    def get_account(self, account_id: str) -> MailAccount | None:
        row = self.session.scalar(select(MailAccountRecord).where(*self._scope(MailAccountRecord), MailAccountRecord.id == account_id))
        return _account(row) if row else None

    def save_account(self, account: MailAccount) -> MailAccount:
        if (account.tenant_id, account.workspace_id, account.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        row = self.session.get(MailAccountRecord, account.id)
        if row is None:
            row = MailAccountRecord(id=account.id, tenant_id=account.tenant_id, workspace_id=account.workspace_id, owner_user_id=account.owner_user_id, created_at=account.created_at)
            self.session.add(row)
        row.address, row.display_name, row.password_enc = account.address, account.display_name, account.password_enc
        row.status, row.last_error, row.last_checked_at = account.status.value, account.last_error, account.last_checked_at
        row.updated_at = account.updated_at
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError:
            raise ValueError("mailbox already connected in this workspace") from None
        return account

    def delete_account(self, account_id: str) -> bool:
        result = self.session.execute(delete(MailAccountRecord).where(*self._scope(MailAccountRecord), MailAccountRecord.id == account_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def list_drafts(self, account_id: str | None, limit: int) -> list[MailDraft]:
        query = select(MailDraftRecord).where(*self._scope(MailDraftRecord))
        if account_id is not None:
            query = query.where(MailDraftRecord.account_id == account_id)
        rows = self.session.scalars(query.order_by(MailDraftRecord.updated_at.desc(), MailDraftRecord.id.desc()).limit(limit))
        return [_draft(row) for row in rows]

    def get_draft(self, draft_id: str) -> MailDraft | None:
        row = self.session.scalar(select(MailDraftRecord).where(*self._scope(MailDraftRecord), MailDraftRecord.id == draft_id))
        return _draft(row) if row else None

    def find_by_key(self, account_id: str, key: str) -> MailDraft | None:
        row = self.session.scalar(
            select(MailDraftRecord).where(*self._scope(MailDraftRecord), MailDraftRecord.account_id == account_id, MailDraftRecord.idempotency_key == key)
        )
        return _draft(row) if row else None

    def save_draft(self, draft: MailDraft) -> MailDraft:
        if (draft.tenant_id, draft.workspace_id, draft.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")
        row = self.session.get(MailDraftRecord, draft.id)
        if row is None:
            row = MailDraftRecord(id=draft.id, tenant_id=draft.tenant_id, workspace_id=draft.workspace_id, owner_user_id=draft.owner_user_id, created_at=draft.created_at)
            self.session.add(row)
        row.account_id, row.kind = draft.account_id, draft.kind
        row.to_addresses, row.cc_addresses, row.bcc_addresses = list(draft.to), list(draft.cc), list(draft.bcc)
        row.subject, row.body = draft.subject, draft.body
        row.in_reply_to, row.references_header = draft.in_reply_to, draft.references
        row.source_folder, row.source_uid = draft.source_folder, draft.source_uid
        row.message_id, row.idempotency_key, row.status = draft.message_id, draft.idempotency_key, draft.status.value
        row.approval_id, row.task_id, row.error = draft.approval_id, draft.task_id, draft.error
        row.saved_to_sent, row.sent_at, row.updated_at = draft.saved_to_sent, draft.sent_at, draft.updated_at
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError:
            raise ValueError("idempotency key already used") from None
        return draft

    def delete_draft(self, draft_id: str) -> bool:
        result = self.session.execute(delete(MailDraftRecord).where(*self._scope(MailDraftRecord), MailDraftRecord.id == draft_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def claim_send(self, draft_id: str) -> bool:
        """Atomically move a draft waiting for approval to ``sending`` (one sender wins)."""
        result = self.session.execute(
            update(MailDraftRecord)
            .where(*self._scope(MailDraftRecord), MailDraftRecord.id == draft_id, MailDraftRecord.status == DraftStatus.PENDING_APPROVAL.value)
            .values(status=DraftStatus.SENDING.value, updated_at=utc_now())
        )
        self.session.flush()
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def count_sends_since(self, account_id: str, since: datetime) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(MailDraftRecord).where(
                    *self._scope(MailDraftRecord),
                    MailDraftRecord.account_id == account_id,
                    MailDraftRecord.status.in_([DraftStatus.SENT.value, DraftStatus.SENDING.value, DraftStatus.UNKNOWN.value]),
                    MailDraftRecord.updated_at >= since,
                )
            )
            or 0
        )
