"""PostgreSQL store of the Email Center (migrations ``0060_email_center`` and ``0062_mail_outgoing``, docs/email.md#email-center).

The session carries the tenant, workspace and user RLS context (``open_mail_store`` opens it
with ``user_scoped=True``); both tables also have a restrictive owner policy, and every query
here repeats the scope and the owner. The mailbox password is stored only as Fernet ciphertext.
State changes of a send (claim, schedule, interrupted, not approved in time) are compare-and-set
updates, so two replicas or a retried run never act on one message twice.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.mailbox.store import INTERRUPTED, NOT_APPROVED_IN_TIME, AccountStatus, DraftStatus, MailAccount, MailAttachment, MailDraft
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
    send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    send_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachment_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_drafts"),)


class MailAttachmentRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_attachments"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(80), ForeignKey("mail_drafts.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    scanned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_attachments"),)


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
        send_at=row.send_at, approved_hash=row.approved_hash, send_lease_until=row.send_lease_until,
        attachment_note=row.attachment_note, created_at=row.created_at, updated_at=row.updated_at,
    )


def _attachment(row: MailAttachmentRecord) -> MailAttachment:
    return MailAttachment(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
        draft_id=row.draft_id, filename=row.filename, content_type=row.content_type, size_bytes=row.size_bytes,
        sha256=row.sha256, storage_key=row.storage_key, scanned=row.scanned, created_at=row.created_at,
    )


class SqlAlchemyMailStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.tenant_id = context.tenant_id
        self.workspace_id = context.workspace_id
        self.owner = context.user_id

    def _scope(self, model: type[MailAccountRecord] | type[MailDraftRecord] | type[MailAttachmentRecord]):  # type: ignore[no-untyped-def]
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
        row.send_at, row.approved_hash, row.send_lease_until = draft.send_at, draft.approved_hash, draft.send_lease_until
        row.attachment_note = draft.attachment_note
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError:
            raise ValueError("idempotency key already used") from None
        return draft

    def delete_draft(self, draft_id: str) -> bool:
        result = self.session.execute(delete(MailDraftRecord).where(*self._scope(MailDraftRecord), MailDraftRecord.id == draft_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def _move(self, draft_id: str, expected: DraftStatus, *conditions: object, **values: object) -> bool:
        """Compare-and-set one draft's status (one replica or one run wins)."""
        result = self.session.execute(
            update(MailDraftRecord)
            .where(*self._scope(MailDraftRecord), MailDraftRecord.id == draft_id, MailDraftRecord.status == expected.value, *conditions)
            .values(updated_at=utc_now(), **values)
        )
        self.session.flush()
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def claim_send(self, draft_id: str, lease_until: datetime) -> bool:
        """Atomically move a draft waiting for approval to ``sending`` (one sender wins)."""
        return self._move(draft_id, DraftStatus.PENDING_APPROVAL, status=DraftStatus.SENDING.value, send_lease_until=lease_until)

    def mark_scheduled(self, draft_id: str, approved_hash: str) -> bool:
        return self._move(draft_id, DraftStatus.PENDING_APPROVAL, status=DraftStatus.SCHEDULED.value, approved_hash=approved_hash, error=None)

    def reset_to_draft(self, draft_id: str, expected: DraftStatus) -> bool:
        return self._move(draft_id, expected, status=DraftStatus.DRAFT.value, approved_hash=None)

    def claim_scheduled(self, draft_id: str, now: datetime, lease_until: datetime) -> bool:
        return self._move(
            draft_id, DraftStatus.SCHEDULED, MailDraftRecord.send_at.is_not(None), MailDraftRecord.send_at <= now,
            status=DraftStatus.SENDING.value, send_lease_until=lease_until,
        )

    def list_scheduled(self, account_id: str | None, limit: int) -> list[MailDraft]:
        query = select(MailDraftRecord).where(
            *self._scope(MailDraftRecord), MailDraftRecord.send_at.is_not(None),
            MailDraftRecord.status.in_([DraftStatus.SCHEDULED.value, DraftStatus.PENDING_APPROVAL.value]),
        )
        if account_id is not None:
            query = query.where(MailDraftRecord.account_id == account_id)
        rows = self.session.scalars(query.order_by(MailDraftRecord.send_at, MailDraftRecord.id).limit(limit))
        return [_draft(row) for row in rows]

    def release_interrupted(self, draft_id: str, now: datetime) -> bool:
        return self._move(
            draft_id, DraftStatus.SENDING, MailDraftRecord.send_lease_until.is_not(None), MailDraftRecord.send_lease_until < now,
            status=DraftStatus.UNKNOWN.value, error=INTERRUPTED, send_lease_until=None,
        )

    def expire_unapproved(self, draft_id: str, before: datetime) -> MailDraft | None:
        previous = self.get_draft(draft_id)
        if previous is None:
            return None
        moved = self._move(
            draft_id, DraftStatus.PENDING_APPROVAL, MailDraftRecord.send_at.is_not(None), MailDraftRecord.send_at < before,
            status=DraftStatus.DRAFT.value, error=NOT_APPROVED_IN_TIME, approval_id=None, task_id=None, idempotency_key=None,
        )
        return previous if moved else None

    def list_attachments(self, draft_id: str) -> list[MailAttachment]:
        rows = self.session.scalars(
            select(MailAttachmentRecord).where(*self._scope(MailAttachmentRecord), MailAttachmentRecord.draft_id == draft_id)
            .order_by(MailAttachmentRecord.created_at, MailAttachmentRecord.id)
        )
        return [_attachment(row) for row in rows]

    def attachments_of_account(self, account_id: str | None) -> list[MailAttachment]:
        query = select(MailAttachmentRecord).join(MailDraftRecord, MailDraftRecord.id == MailAttachmentRecord.draft_id).where(
            *self._scope(MailAttachmentRecord), *self._scope(MailDraftRecord)
        )
        if account_id is not None:
            query = query.where(MailDraftRecord.account_id == account_id)
        return [_attachment(row) for row in self.session.scalars(query)]

    def get_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None:
        row = self.session.scalar(
            select(MailAttachmentRecord).where(
                *self._scope(MailAttachmentRecord), MailAttachmentRecord.draft_id == draft_id, MailAttachmentRecord.id == attachment_id
            )
        )
        return _attachment(row) if row else None

    def save_attachment(self, attachment: MailAttachment) -> MailAttachment:
        if (attachment.tenant_id, attachment.workspace_id, attachment.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")
        if self.get_draft(attachment.draft_id) is None:
            raise LookupError("draft not found")
        self.session.add(MailAttachmentRecord(
            id=attachment.id, tenant_id=attachment.tenant_id, workspace_id=attachment.workspace_id, owner_user_id=attachment.owner_user_id,
            draft_id=attachment.draft_id, filename=attachment.filename, content_type=attachment.content_type,
            size_bytes=attachment.size_bytes, sha256=attachment.sha256, storage_key=attachment.storage_key,
            scanned=attachment.scanned, created_at=attachment.created_at,
        ))
        self.session.flush()
        return attachment

    def delete_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None:
        found = self.get_attachment(draft_id, attachment_id)
        if found is None:
            return None
        self.session.execute(delete(MailAttachmentRecord).where(*self._scope(MailAttachmentRecord), MailAttachmentRecord.id == attachment_id))
        return found

    def pending_attachment_bytes(self) -> int:
        return int(
            self.session.scalar(
                select(func.coalesce(func.sum(MailAttachmentRecord.size_bytes), 0))
                .join(MailDraftRecord, MailDraftRecord.id == MailAttachmentRecord.draft_id)
                .where(*self._scope(MailAttachmentRecord), MailDraftRecord.status != DraftStatus.SENT.value)
            )
            or 0
        )

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
