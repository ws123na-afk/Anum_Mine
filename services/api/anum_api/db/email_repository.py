"""PostgreSQL store of the unified inbox (migration 0035_email_inbox, docs/email.md).

Every table is scoped to a tenant and workspace under forced RLS; the session already
carries that context (``open_scoped_store``) and every query filters on it as well. Who
may see a *private* mailbox inside the workspace is decided by ``email_inbox.can_see``.
Tokens are stored only as Fernet ciphertext (``ANUM_SECRETS_KEY``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.email_inbox import (
    DraftStatus,
    EmailAccount,
    EmailDraft,
    EmailMessage,
    MessageFilters,
    OAuthPending,
    _merge,
)
from anum_api.email_providers import EmailProvider
from anum_api.schemas import TenantContext, utc_now

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class EmailAccountRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "email_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    connected_by: Mapped[str] = mapped_column(String(160), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    provider_account_id: Mapped[str | None] = mapped_column(String(200))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("email_accounts"),)


class EmailMessageRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(300), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(300), nullable=False)
    folder_id: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    from_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    from_name: Mapped[str | None] = mapped_column(String(200))
    to: Mapped[list[str]] = mapped_column("to_addresses", JSONB, nullable=False, default=list)
    cc: Mapped[list[str]] = mapped_column("cc_addresses", JSONB, nullable=False, default=list)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    internet_message_id: Mapped[str | None] = mapped_column(String(500))
    references: Mapped[str | None] = mapped_column("references_header", Text)
    bulk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category: Mapped[str | None] = mapped_column(String(30))
    priority: Mapped[str | None] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(Text)
    triage_source: Mapped[str | None] = mapped_column(String(10))
    triage_model: Mapped[str | None] = mapped_column(String(160))
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("email_messages"),)


class EmailDraftRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "email_drafts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("email_messages.id", ondelete="SET NULL"))
    thread_id: Mapped[str | None] = mapped_column(String(300))
    to: Mapped[list[str]] = mapped_column("to_addresses", JSONB, nullable=False, default=list)
    cc: Mapped[list[str]] = mapped_column("cc_addresses", JSONB, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(160))
    provider_draft_id: Mapped[str | None] = mapped_column(String(300))
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("email_drafts"),)


class EmailOAuthRequestRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "email_oauth_requests"

    nonce: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    code_verifier_enc: Mapped[str | None] = mapped_column(Text)
    code_enc: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("email_oauth_requests"),)


_ACCOUNT_FIELDS = (
    "provider", "address", "display_name", "connected_by", "visibility", "status", "scopes", "provider_account_id",
    "access_token_enc", "refresh_token_enc", "access_expires_at", "sync_cursor", "last_sync_at", "last_sync_status",
    "last_error", "sync_lease_until", "created_at", "updated_at",
)
_MESSAGE_FIELDS = (
    "account_id", "provider_message_id", "thread_id", "folder_id", "subject", "from_address", "from_name", "to", "cc",
    "snippet", "labels", "received_at", "is_read", "is_archived", "has_attachments", "internet_message_id", "references",
    "bulk", "category", "priority", "summary", "triage_source", "triage_model", "triaged_at", "created_at", "updated_at",
)
_DRAFT_FIELDS = (
    "account_id", "message_id", "thread_id", "to", "cc", "subject", "body", "status", "source", "model_provider",
    "model_name", "provider_draft_id", "approval_id", "task_id", "created_by", "error", "sent_at", "created_at", "updated_at",
)
_PENDING_FIELDS = ("user_id", "provider", "visibility", "code_verifier_enc", "code_enc", "error", "created_at", "expires_at")


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _copy(source: Any, record: Any, fields: tuple[str, ...]) -> None:
    for name in fields:
        value = getattr(source, name)
        setattr(record, name, list(value) if isinstance(value, list) else _plain(value) if value is not None else None)


def _values(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(record, name) for name in fields}


def _account(record: EmailAccountRecord) -> EmailAccount:
    return EmailAccount(id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _ACCOUNT_FIELDS))


def _message(record: EmailMessageRecord) -> EmailMessage:
    return EmailMessage(id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _MESSAGE_FIELDS))


def _draft(record: EmailDraftRecord) -> EmailDraft:
    return EmailDraft(id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _DRAFT_FIELDS))


def _pending(record: EmailOAuthRequestRecord) -> OAuthPending:
    return OAuthPending(nonce=record.nonce, tenant_id=record.tenant_id, workspace_id=record.workspace_id, **_values(record, _PENDING_FIELDS))


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlAlchemyEmailStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, model: Any) -> tuple[Any, Any]:
        return (model.tenant_id == self.context.tenant_id, model.workspace_id == self.context.workspace_id)

    # accounts ------------------------------------------------------------------------------

    def list_accounts(self) -> list[EmailAccount]:
        records = self.session.scalars(
            select(EmailAccountRecord).where(*self._scope(EmailAccountRecord)).order_by(EmailAccountRecord.created_at, EmailAccountRecord.id)
        ).all()
        return [_account(r) for r in records]

    def _account_record(self, account_id: str, *, lock: bool = False) -> EmailAccountRecord | None:
        statement = select(EmailAccountRecord).where(*self._scope(EmailAccountRecord), EmailAccountRecord.id == account_id)
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def get_account(self, account_id: str) -> EmailAccount | None:
        record = self._account_record(account_id)
        return _account(record) if record is not None else None

    def find_account(self, provider: EmailProvider, address: str) -> EmailAccount | None:
        record = self.session.scalar(
            select(EmailAccountRecord).where(
                *self._scope(EmailAccountRecord), EmailAccountRecord.provider == provider.value, EmailAccountRecord.address == address
            )
        )
        return _account(record) if record is not None else None

    def add_account(self, account: EmailAccount) -> EmailAccount:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = EmailAccountRecord(id=account.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(account, record, _ACCOUNT_FIELDS)
        self.session.add(record)
        self.session.flush()
        return account

    def save_account(self, account: EmailAccount) -> EmailAccount:
        record = self._account_record(account.id, lock=True)
        if record is None:
            raise KeyError(account.id)
        _copy(account, record, _ACCOUNT_FIELDS)
        self.session.flush()
        return account

    def delete_account(self, account_id: str) -> None:
        # Messages and drafts go with it (on delete cascade).
        self.session.execute(delete(EmailAccountRecord).where(*self._scope(EmailAccountRecord), EmailAccountRecord.id == account_id))
        self.session.flush()

    def claim_sync(self, account_id: str, now: datetime, until: datetime) -> bool:
        claimed = self.session.execute(
            update(EmailAccountRecord)
            .where(
                *self._scope(EmailAccountRecord),
                EmailAccountRecord.id == account_id,
                or_(EmailAccountRecord.sync_lease_until.is_(None), EmailAccountRecord.sync_lease_until <= now),
            )
            .values(sync_lease_until=until)
            .returning(EmailAccountRecord.id)
        ).first()
        return claimed is not None

    # messages ------------------------------------------------------------------------------

    def upsert_messages(self, messages: list[EmailMessage]) -> int:
        if not messages:
            return 0
        created = 0
        by_account: dict[str, list[EmailMessage]] = {}
        for message in messages:
            by_account.setdefault(message.account_id, []).append(message)
        for account_id, items in by_account.items():
            ids = [m.provider_message_id for m in items]
            existing = {
                r.provider_message_id: r
                for r in self.session.scalars(
                    select(EmailMessageRecord).where(
                        *self._scope(EmailMessageRecord),
                        EmailMessageRecord.account_id == account_id,
                        EmailMessageRecord.provider_message_id.in_(ids),
                    )
                ).all()
            }
            for message in items:
                record = existing.get(message.provider_message_id)
                if record is None:
                    record = EmailMessageRecord(id=message.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
                    _copy(message, record, _MESSAGE_FIELDS)
                    self.session.add(record)
                    existing[message.provider_message_id] = record
                    created += 1
                else:
                    _copy(_merge(_message(record), message), record, _MESSAGE_FIELDS)
        self.session.flush()
        return created

    def apply_changes(self, account_id: str, changes: dict[str, tuple[bool | None, bool | None]]) -> None:
        now = utc_now()
        for provider_id, (read, archived) in changes.items():
            values: dict[str, Any] = {"updated_at": now}
            if read is not None:
                values["is_read"] = read
            if archived is not None:
                values["is_archived"] = archived
            if len(values) == 1:
                continue
            self.session.execute(
                update(EmailMessageRecord)
                .where(
                    *self._scope(EmailMessageRecord),
                    EmailMessageRecord.account_id == account_id,
                    EmailMessageRecord.provider_message_id == provider_id,
                )
                .values(**values)
            )
        self.session.flush()

    def delete_provider_messages(self, account_id: str, provider_ids: list[str]) -> None:
        if provider_ids:
            self.session.execute(
                delete(EmailMessageRecord).where(
                    *self._scope(EmailMessageRecord),
                    EmailMessageRecord.account_id == account_id,
                    EmailMessageRecord.provider_message_id.in_(provider_ids),
                )
            )
            self.session.flush()

    def _message_record(self, message_id: str) -> EmailMessageRecord | None:
        return self.session.scalar(
            select(EmailMessageRecord).where(*self._scope(EmailMessageRecord), EmailMessageRecord.id == message_id)
        )

    def get_message(self, message_id: str) -> EmailMessage | None:
        record = self._message_record(message_id)
        return _message(record) if record is not None else None

    def get_by_provider_id(self, account_id: str, provider_id: str) -> EmailMessage | None:
        record = self.session.scalar(
            select(EmailMessageRecord).where(
                *self._scope(EmailMessageRecord),
                EmailMessageRecord.account_id == account_id,
                EmailMessageRecord.provider_message_id == provider_id,
            )
        )
        return _message(record) if record is not None else None

    def save_message(self, message: EmailMessage) -> EmailMessage:
        record = self._message_record(message.id)
        if record is None:
            raise KeyError(message.id)
        _copy(message, record, _MESSAGE_FIELDS)
        self.session.flush()
        return message

    def list_messages(
        self, account_ids: list[str], filters: MessageFilters, *, limit: int, before: EmailMessage | None
    ) -> list[EmailMessage]:
        if not account_ids:
            return []
        m = EmailMessageRecord
        statement = select(m).where(*self._scope(m), m.account_id.in_(account_ids), m.is_archived.is_(filters.archived))
        if filters.account_id:
            statement = statement.where(m.account_id == filters.account_id)
        if filters.category:
            statement = statement.where(m.category == filters.category.value)
        if filters.priority:
            statement = statement.where(m.priority == filters.priority.value)
        if filters.unread is not None:
            statement = statement.where(m.is_read.is_(not filters.unread))
        if filters.query:
            pattern = _like(filters.query)
            statement = statement.where(
                or_(
                    m.subject.ilike(pattern, escape="\\"),
                    m.from_address.ilike(pattern, escape="\\"),
                    m.from_name.ilike(pattern, escape="\\"),
                    m.snippet.ilike(pattern, escape="\\"),
                )
            )
        if before is not None:
            statement = statement.where(
                or_(m.received_at < before.received_at, and_(m.received_at == before.received_at, m.id < before.id))
            )
        records = self.session.scalars(statement.order_by(m.received_at.desc(), m.id.desc()).limit(limit)).all()
        return [_message(r) for r in records]

    def thread(self, account_id: str, thread_id: str, limit: int) -> list[EmailMessage]:
        m = EmailMessageRecord
        records = self.session.scalars(
            select(m)
            .where(*self._scope(m), m.account_id == account_id, m.thread_id == thread_id)
            .order_by(m.received_at.desc(), m.id.desc())
            .limit(limit)
        ).all()
        return [_message(r) for r in reversed(records)]

    def counts(self, account_ids: list[str]) -> dict[str, tuple[int, int]]:
        if not account_ids:
            return {}
        m = EmailMessageRecord
        rows = self.session.execute(
            select(
                m.account_id,
                func.count().filter(m.is_archived.is_(False)),
                func.count().filter(and_(m.is_archived.is_(False), m.is_read.is_(False))),
            )
            .where(*self._scope(m), m.account_id.in_(account_ids))
            .group_by(m.account_id)
        ).all()
        found = {row[0]: (int(row[1]), int(row[2])) for row in rows}
        return {account_id: found.get(account_id, (0, 0)) for account_id in account_ids}

    # drafts --------------------------------------------------------------------------------

    def add_draft(self, draft: EmailDraft) -> EmailDraft:
        record = EmailDraftRecord(id=draft.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(draft, record, _DRAFT_FIELDS)
        self.session.add(record)
        self.session.flush()
        return draft

    def _draft_record(self, draft_id: str) -> EmailDraftRecord | None:
        return self.session.scalar(
            select(EmailDraftRecord).where(*self._scope(EmailDraftRecord), EmailDraftRecord.id == draft_id).with_for_update()
        )

    def get_draft(self, draft_id: str) -> EmailDraft | None:
        record = self.session.scalar(select(EmailDraftRecord).where(*self._scope(EmailDraftRecord), EmailDraftRecord.id == draft_id))
        return _draft(record) if record is not None else None

    def save_draft(self, draft: EmailDraft) -> EmailDraft:
        record = self._draft_record(draft.id)
        if record is None:
            raise KeyError(draft.id)
        _copy(draft, record, _DRAFT_FIELDS)
        self.session.flush()
        return draft

    def list_drafts(self, account_ids: list[str], *, message_id: str | None, limit: int) -> list[EmailDraft]:
        if not account_ids:
            return []
        d = EmailDraftRecord
        statement = select(d).where(*self._scope(d), d.account_id.in_(account_ids), d.status != DraftStatus.DISCARDED.value)
        if message_id is not None:
            statement = statement.where(d.message_id == message_id)
        records = self.session.scalars(statement.order_by(d.created_at.desc(), d.id.desc()).limit(limit)).all()
        return [_draft(r) for r in records]

    # pending connections -------------------------------------------------------------------

    def add_pending(self, pending: OAuthPending) -> OAuthPending:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        # Housekeeping: expired requests of this workspace go first.
        self.session.execute(
            delete(EmailOAuthRequestRecord).where(*self._scope(EmailOAuthRequestRecord), EmailOAuthRequestRecord.expires_at < utc_now())
        )
        record = EmailOAuthRequestRecord(nonce=pending.nonce, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        _copy(pending, record, _PENDING_FIELDS)
        self.session.add(record)
        self.session.flush()
        return pending

    def get_pending(self, nonce: str) -> OAuthPending | None:
        record = self.session.scalar(
            select(EmailOAuthRequestRecord).where(*self._scope(EmailOAuthRequestRecord), EmailOAuthRequestRecord.nonce == nonce)
        )
        return _pending(record) if record is not None else None

    def save_pending(self, pending: OAuthPending) -> OAuthPending:
        record = self.session.scalar(
            select(EmailOAuthRequestRecord)
            .where(*self._scope(EmailOAuthRequestRecord), EmailOAuthRequestRecord.nonce == pending.nonce)
            .with_for_update()
        )
        if record is None:
            raise KeyError(pending.nonce)
        _copy(pending, record, _PENDING_FIELDS)
        self.session.flush()
        return pending

    def delete_pending(self, nonce: str) -> None:
        self.session.execute(
            delete(EmailOAuthRequestRecord).where(*self._scope(EmailOAuthRequestRecord), EmailOAuthRequestRecord.nonce == nonce)
        )
        self.session.flush()
