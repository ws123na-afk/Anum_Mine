"""PostgreSQL store of the Email Center (migrations ``0060_email_center``,
``0062_mail_outgoing`` and ``0067_mail_sharing_contacts``, docs/email.md#email-center).

The session carries the tenant, workspace and user RLS context (``open_mail_store`` opens it
with ``user_scoped=True``); every table also has restrictive owner policies (a mailbox is
readable by the people its owner shared it with), and every query here repeats the scope and
the owner or grantee. The mailbox password is stored only as Fernet ciphertext. State changes
of a send (claim, schedule, interrupted, not approved in time) are compare-and-set updates, so
two replicas or a retried run never act on one message twice.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.mailbox.store import (
    INTERRUPTED,
    NOT_APPROVED_IN_TIME,
    Access,
    AccountStatus,
    DraftStatus,
    MailAccount,
    MailAttachment,
    MailContact,
    MailDraft,
    MailGrant,
    MailIdentity,
)
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
    # Set while the owner disconnects it (migration 0069): every writer's files are collected.
    disconnecting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    identity_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("mail_identities.id", ondelete="SET NULL"))
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


def _account_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "account_id", "owner_user_id"],
        ["mail_accounts.tenant_id", "mail_accounts.workspace_id", "mail_accounts.id", "mail_accounts.owner_user_id"],
        name=f"fk_{table}_account", ondelete="CASCADE",
    )


class MailGrantRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_account_grants"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    grantee_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    access: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_account_grants"), _account_fk("mail_account_grants"))


class MailIdentityRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_identities"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_identities"), _account_fk("mail_identities"))


class MailContactRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "mail_contacts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(60), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("mail_contacts"),)


def _grant(row: MailGrantRecord) -> MailGrant:
    return MailGrant(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, account_id=row.account_id,
        grantee_user_id=row.grantee_user_id, access=row.access, created_at=row.created_at, updated_at=row.updated_at,  # type: ignore[arg-type]
    )


def _identity(row: MailIdentityRecord) -> MailIdentity:
    return MailIdentity(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, account_id=row.account_id,
        address=row.address, display_name=row.display_name, signature=row.signature, is_default=row.is_default,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _contact(row: MailContactRecord) -> MailContact:
    return MailContact(
        id=row.id, tenant_id=row.tenant_id, workspace_id=row.workspace_id, owner_user_id=row.owner_user_id, name=row.name,
        email=row.email, company=row.company, phone=row.phone, notes=row.notes, created_at=row.created_at, updated_at=row.updated_at,
    )


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


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
        source_folder=row.source_folder, source_uid=row.source_uid, message_id=row.message_id, identity_id=row.identity_id,
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

    def _scope(self, model):  # type: ignore[no-untyped-def]
        return (model.tenant_id == self.tenant_id, model.workspace_id == self.workspace_id, model.owner_user_id == self.owner)

    def _workspace(self, model):  # type: ignore[no-untyped-def]
        return (model.tenant_id == self.tenant_id, model.workspace_id == self.workspace_id)

    def _check_scope(self, item: MailAccount | MailDraft | MailGrant | MailIdentity | MailContact) -> None:
        if (item.tenant_id, item.workspace_id, item.owner_user_id) != (self.tenant_id, self.workspace_id, self.owner):
            raise PermissionError("row outside the caller's scope")

    def _flush(self, message: str) -> None:
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError:
            raise ValueError(message) from None

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

    def mark_disconnecting(self, account_id: str) -> None:
        self.session.execute(
            update(MailAccountRecord).where(*self._scope(MailAccountRecord), MailAccountRecord.id == account_id).values(disconnecting_at=utc_now())
        )

    def delete_account(self, account_id: str) -> bool:
        result = self.session.execute(delete(MailAccountRecord).where(*self._scope(MailAccountRecord), MailAccountRecord.id == account_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # -- shared mailboxes -------------------------------------------------------------------

    def list_shared_accounts(self) -> list[tuple[MailAccount, MailGrant]]:
        rows = self.session.execute(
            select(MailAccountRecord, MailGrantRecord)
            .join(MailGrantRecord, MailGrantRecord.account_id == MailAccountRecord.id)
            .where(*self._workspace(MailAccountRecord), *self._workspace(MailGrantRecord), MailGrantRecord.grantee_user_id == self.owner)
            .order_by(MailAccountRecord.created_at)
        ).all()
        return [(_account(account), _grant(grant)) for account, grant in rows]

    def get_accessible_account(self, account_id: str) -> tuple[MailAccount, Access] | None:
        row = self.session.scalar(select(MailAccountRecord).where(*self._workspace(MailAccountRecord), MailAccountRecord.id == account_id))
        if row is None:
            return None
        if row.owner_user_id == self.owner:
            return _account(row), "owner"
        access = self.session.scalar(
            select(MailGrantRecord.access).where(
                *self._workspace(MailGrantRecord), MailGrantRecord.account_id == account_id, MailGrantRecord.grantee_user_id == self.owner
            )
        )
        return (_account(row), access) if access in {"read", "send"} else None  # type: ignore[return-value]

    def list_grants(self, account_id: str) -> list[MailGrant]:
        rows = self.session.scalars(
            select(MailGrantRecord).where(*self._scope(MailGrantRecord), MailGrantRecord.account_id == account_id).order_by(MailGrantRecord.created_at, MailGrantRecord.id)
        )
        return [_grant(row) for row in rows]

    def _visible_grant(self, grant_id: str) -> MailGrantRecord | None:
        return self.session.scalar(
            select(MailGrantRecord).where(
                *self._workspace(MailGrantRecord), MailGrantRecord.id == grant_id,
                or_(MailGrantRecord.owner_user_id == self.owner, MailGrantRecord.grantee_user_id == self.owner),
            )
        )

    def get_grant(self, grant_id: str) -> MailGrant | None:
        row = self._visible_grant(grant_id)
        return _grant(row) if row else None

    def save_grant(self, grant: MailGrant) -> MailGrant:
        self._check_scope(grant)
        row = self.session.get(MailGrantRecord, grant.id)
        if row is None:
            row = MailGrantRecord(id=grant.id, tenant_id=grant.tenant_id, workspace_id=grant.workspace_id, owner_user_id=grant.owner_user_id, created_at=grant.created_at)
            self.session.add(row)
        row.account_id, row.grantee_user_id, row.access, row.updated_at = grant.account_id, grant.grantee_user_id, grant.access, grant.updated_at
        self._flush("already shared with this person")
        return grant

    def delete_grant(self, grant_id: str) -> bool:
        result = self.session.execute(
            delete(MailGrantRecord).where(
                *self._workspace(MailGrantRecord), MailGrantRecord.id == grant_id,
                or_(MailGrantRecord.owner_user_id == self.owner, MailGrantRecord.grantee_user_id == self.owner),
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # -- identities -------------------------------------------------------------------------

    def _identities(self):  # type: ignore[no-untyped-def]
        # The owner's, or a mailbox shared with the caller to send (the row policy says the same).
        sender = select(MailGrantRecord.account_id).where(
            *self._workspace(MailGrantRecord), MailGrantRecord.grantee_user_id == self.owner, MailGrantRecord.access == "send"
        )
        return select(MailIdentityRecord).where(
            *self._workspace(MailIdentityRecord),
            or_(MailIdentityRecord.owner_user_id == self.owner, MailIdentityRecord.account_id.in_(sender)),
        )

    def list_identities(self, account_id: str) -> list[MailIdentity]:
        rows = self.session.scalars(
            self._identities().where(MailIdentityRecord.account_id == account_id)
            .order_by(MailIdentityRecord.is_default.desc(), MailIdentityRecord.address, MailIdentityRecord.id)
        )
        return [_identity(row) for row in rows]

    def get_identity(self, identity_id: str) -> MailIdentity | None:
        row = self.session.scalar(self._identities().where(MailIdentityRecord.id == identity_id))
        return _identity(row) if row else None

    def save_identity(self, identity: MailIdentity) -> MailIdentity:
        self._check_scope(identity)
        row = self.session.get(MailIdentityRecord, identity.id)
        if row is None:
            row = MailIdentityRecord(id=identity.id, tenant_id=identity.tenant_id, workspace_id=identity.workspace_id, owner_user_id=identity.owner_user_id, created_at=identity.created_at)
            self.session.add(row)
        row.account_id, row.address, row.display_name = identity.account_id, identity.address, identity.display_name
        row.signature, row.is_default, row.updated_at = identity.signature, identity.is_default, identity.updated_at
        self._flush("this mailbox already sends as that address")
        return identity

    def delete_identity(self, identity_id: str) -> bool:
        result = self.session.execute(delete(MailIdentityRecord).where(*self._scope(MailIdentityRecord), MailIdentityRecord.id == identity_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # -- contacts ---------------------------------------------------------------------------

    def list_contacts(self, query: str | None, after: tuple[str, str] | None, limit: int) -> tuple[list[MailContact], int]:
        # Byte order ("C"), like the Python sort of the memory store, so a cursor means the same in both.
        sort = func.lower(func.coalesce(func.nullif(MailContactRecord.name, ""), MailContactRecord.email)).collate("C")
        ident = MailContactRecord.id.collate("C")
        base = select(MailContactRecord).where(*self._scope(MailContactRecord))
        if query:
            pattern = _like(query)
            base = base.where(
                or_(
                    MailContactRecord.name.ilike(pattern, escape="\\"),
                    MailContactRecord.email.ilike(pattern, escape="\\"),
                    MailContactRecord.company.ilike(pattern, escape="\\"),
                )
            )
        total = int(self.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        page = base
        if after is not None:
            page = page.where(or_(sort > after[0], and_(sort == after[0], ident > after[1])))
        rows = self.session.scalars(page.order_by(sort, ident).limit(limit))
        return [_contact(row) for row in rows], total

    def get_contact(self, contact_id: str) -> MailContact | None:
        row = self.session.scalar(select(MailContactRecord).where(*self._scope(MailContactRecord), MailContactRecord.id == contact_id))
        return _contact(row) if row else None

    def find_contact(self, email: str) -> MailContact | None:
        row = self.session.scalar(select(MailContactRecord).where(*self._scope(MailContactRecord), MailContactRecord.email == email.lower()))
        return _contact(row) if row else None

    def save_contact(self, contact: MailContact) -> MailContact:
        self._check_scope(contact)
        require_workspace(self.session, self.tenant_id, self.workspace_id)
        row = self.session.get(MailContactRecord, contact.id)
        if row is None:
            row = MailContactRecord(id=contact.id, tenant_id=contact.tenant_id, workspace_id=contact.workspace_id, owner_user_id=contact.owner_user_id, created_at=contact.created_at)
            self.session.add(row)
        row.name, row.email, row.company, row.phone, row.notes = contact.name, contact.email, contact.company, contact.phone, contact.notes
        row.updated_at = contact.updated_at
        self._flush("a contact with this address already exists")
        return contact

    def delete_contact(self, contact_id: str) -> bool:
        result = self.session.execute(delete(MailContactRecord).where(*self._scope(MailContactRecord), MailContactRecord.id == contact_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def count_contacts(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(MailContactRecord).where(*self._scope(MailContactRecord))) or 0)

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
        row.identity_id = draft.identity_id
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
