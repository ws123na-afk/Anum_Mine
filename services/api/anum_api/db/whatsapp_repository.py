"""PostgreSQL WhatsApp inbox (migration 0033_whatsapp, docs/whatsapp.md#storage).

The session carries the tenant and workspace RLS context (``open_scoped_store``); the
queries filter on the scope explicitly as well. The access token and the app secret are
encrypted with ``ANUM_SECRETS_KEY`` (``anum_api.secret_box``) and decrypted only when a
caller asks for them (sending, the webhook signature check).

``discover_routes`` and ``discover_verify_token`` are the webhook's only cross-tenant
reads: short read-only transactions as ``anum_maintenance``, which can see nothing of
``whatsapp_accounts`` but the scope, phone number id and verify token hash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    and_,
    bindparam,
    exists,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.maintenance import default_session_factory, discover
from anum_api.schemas import TenantContext
from anum_api.secret_box import SecretCipher
from anum_api.whatsapp_store import (
    AccountStatus,
    Category,
    Contact,
    Conversation,
    ConversationFilter,
    Direction,
    MediaInfo,
    Message,
    MessageStatus,
    PhoneNumberTakenError,
    Suggestion,
    TemplateUse,
    WhatsAppAccount,
    secret_hint,
    without_secrets,
)

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace

from pydantic import SecretStr


class WhatsAppAccountRecord(Base):
    __tablename__ = "whatsapp_accounts"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    phone_number_id: Mapped[str] = mapped_column(String(40), nullable=False)
    business_account_id: Mapped[str] = mapped_column(String(40), nullable=False)
    display_phone_number: Mapped[str | None] = mapped_column(String(40))
    verified_name: Mapped[str | None] = mapped_column(String(200))
    quality_rating: Mapped[str | None] = mapped_column(String(20))
    access_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_hint: Mapped[str | None] = mapped_column(String(16))
    app_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    verify_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_test_number: Mapped[str | None] = mapped_column(String(20))
    auto_send_templates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    triage_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (_workspace_fk("whatsapp_accounts"),)


class WhatsAppContactRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "whatsapp_contacts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("whatsapp_contacts"),)


class WhatsAppConversationRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "whatsapp_conversations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(80), nullable=False)
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_message_preview: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_direction: Mapped[str] = mapped_column(String(10), nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str | None] = mapped_column(String(20))
    category_source: Mapped[str | None] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    needs_triage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("whatsapp_conversations"),)


class WhatsAppMessageRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "whatsapp_messages"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    wamid: Mapped[str | None] = mapped_column(String(200))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    media: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    template: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    reply_to: Mapped[str | None] = mapped_column(String(200))
    sent_by: Mapped[str | None] = mapped_column(String(160))
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("whatsapp_messages"),)


PHONE_UNIQUE = "uq_whatsapp_accounts_phone_number_id"
WAMID_UNIQUE = "uq_whatsapp_messages_wamid"

_ACCOUNT_FIELDS = (
    "phone_number_id", "business_account_id", "display_phone_number", "verified_name", "quality_rating",
    "verify_token_hash", "owner_test_number", "triage_enabled", "last_error", "last_checked_at",
    "last_webhook_at", "created_at", "updated_at", "updated_by",
)
_CONVERSATION_FIELDS = (
    "contact_id", "wa_id", "contact_name", "last_message_at", "last_message_preview", "last_inbound_at",
    "unread_count", "category_source", "summary", "needs_triage", "triaged_at", "created_at", "updated_at",
)
_MESSAGE_FIELDS = (
    "conversation_id", "wamid", "kind", "body", "error", "reply_to", "sent_by", "approval_id", "task_id",
    "timestamp", "created_at", "updated_at",
)


def _escape_like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _conversation(record: WhatsAppConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        contact_id=record.contact_id,
        wa_id=record.wa_id,
        contact_name=record.contact_name,
        last_message_at=record.last_message_at,
        last_message_preview=record.last_message_preview,
        last_direction=Direction(record.last_direction),
        last_inbound_at=record.last_inbound_at,
        unread_count=record.unread_count,
        category=Category(record.category) if record.category else None,
        category_source=record.category_source,  # type: ignore[arg-type]
        summary=record.summary,
        suggestion=Suggestion.model_validate(record.suggestion) if record.suggestion else None,
        needs_triage=record.needs_triage,
        triaged_at=record.triaged_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message(record: WhatsAppMessageRecord) -> Message:
    return Message(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        conversation_id=record.conversation_id,
        wamid=record.wamid,
        direction=Direction(record.direction),
        kind=record.kind,
        body=record.body,
        media=MediaInfo.model_validate(record.media) if record.media else None,
        template=TemplateUse.model_validate(record.template) if record.template else None,
        status=MessageStatus(record.status),
        error=record.error,
        reply_to=record.reply_to,
        sent_by=record.sent_by,
        approval_id=record.approval_id,
        task_id=record.task_id,
        timestamp=record.timestamp,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class SqlAlchemyWhatsAppStore:
    def __init__(self, session: Session, context: TenantContext, cipher: SecretCipher) -> None:
        self.session = session
        self.context = context
        self.cipher = cipher

    def _scoped(self, model: Any) -> tuple[Any, Any]:
        return (model.tenant_id == self.context.tenant_id, model.workspace_id == self.context.workspace_id)

    # -- account -------------------------------------------------------------------------

    def _account_record(self, *, lock: bool = False) -> WhatsAppAccountRecord | None:
        statement = select(WhatsAppAccountRecord).where(*self._scoped(WhatsAppAccountRecord))
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def get_account(self, *, include_secrets: bool = False) -> WhatsAppAccount | None:
        record = self._account_record()
        if record is None:
            return None
        account = WhatsAppAccount(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            access_token_hint=record.access_token_hint,
            auto_send_templates=list(record.auto_send_templates or []),
            status=AccountStatus(record.status),
            **{name: getattr(record, name) for name in _ACCOUNT_FIELDS},
        )
        if include_secrets:
            account.access_token = SecretStr(self.cipher.decrypt(record.access_token_ciphertext))
            account.app_secret = SecretStr(self.cipher.decrypt(record.app_secret_ciphertext))
        return account

    def save_account(self, account: WhatsAppAccount, *, access_token: str | None, app_secret: str | None) -> WhatsAppAccount:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = self._account_record(lock=True)
        if record is None:
            if access_token is None or app_secret is None:
                raise ValueError("a new account needs the access token and the app secret")
            record = WhatsAppAccountRecord(tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
            self.session.add(record)
        for name in _ACCOUNT_FIELDS:
            setattr(record, name, getattr(account, name))
        record.auto_send_templates = list(account.auto_send_templates)
        record.status = account.status.value
        if access_token is not None:
            record.access_token_ciphertext = self.cipher.encrypt(access_token)
            record.access_token_hint = secret_hint(access_token)
        if app_secret is not None:
            record.app_secret_ciphertext = self.cipher.encrypt(app_secret)
        try:
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError as exc:
            if PHONE_UNIQUE in str(exc.orig):
                raise PhoneNumberTakenError(account.phone_number_id) from None
            raise
        saved = account.model_copy(update=without_secrets())
        saved.access_token_hint = record.access_token_hint
        return saved

    def delete_account(self) -> bool:
        record = self._account_record(lock=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    # -- contacts and conversations ------------------------------------------------------

    def upsert_contact(self, wa_id: str, profile_name: str | None, now: datetime, new_id: str) -> Contact:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = self.session.scalar(
            select(WhatsAppContactRecord)
            .where(*self._scoped(WhatsAppContactRecord), WhatsAppContactRecord.wa_id == wa_id)
            .with_for_update()
        )
        if record is None:
            record = WhatsAppContactRecord(
                id=new_id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id,
                wa_id=wa_id, profile_name=profile_name, created_at=now, updated_at=now,
            )
            self.session.add(record)
        elif profile_name and profile_name != record.profile_name:
            record.profile_name = profile_name
            record.updated_at = now
        self.session.flush()
        return Contact(
            id=record.id, tenant_id=record.tenant_id, workspace_id=record.workspace_id, wa_id=record.wa_id,
            profile_name=record.profile_name, created_at=record.created_at, updated_at=record.updated_at,
        )

    def _conversation_record(self, *conditions: Any, lock: bool = False) -> WhatsAppConversationRecord | None:
        statement = select(WhatsAppConversationRecord).where(*self._scoped(WhatsAppConversationRecord), *conditions)
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def conversation_for(self, wa_id: str) -> Conversation | None:
        record = self._conversation_record(WhatsAppConversationRecord.wa_id == wa_id, lock=True)
        return _conversation(record) if record is not None else None

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        record = self._conversation_record(WhatsAppConversationRecord.id == conversation_id)
        return _conversation(record) if record is not None else None

    @staticmethod
    def _copy_conversation(conversation: Conversation, record: WhatsAppConversationRecord) -> None:
        for name in _CONVERSATION_FIELDS:
            setattr(record, name, getattr(conversation, name))
        record.last_direction = conversation.last_direction.value
        record.category = conversation.category.value if conversation.category else None
        record.suggestion = conversation.suggestion.model_dump(mode="json") if conversation.suggestion else None

    def add_conversation(self, conversation: Conversation) -> Conversation:
        record = WhatsAppConversationRecord(
            id=conversation.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id
        )
        self._copy_conversation(conversation, record)
        self.session.add(record)
        self.session.flush()
        return conversation

    def save_conversation(self, conversation: Conversation) -> Conversation:
        record = self._conversation_record(WhatsAppConversationRecord.id == conversation.id, lock=True)
        if record is None:
            raise KeyError(conversation.id)
        self._copy_conversation(conversation, record)
        self.session.flush()
        return conversation

    def list_conversations(
        self, *, limit: int, before: Conversation | None, query: str | None, filter: ConversationFilter
    ) -> list[Conversation]:
        model = WhatsAppConversationRecord
        statement = select(model).where(*self._scoped(model))
        if before is not None:
            statement = statement.where(
                or_(
                    model.last_message_at < before.last_message_at,
                    and_(model.last_message_at == before.last_message_at, model.id < before.id),
                )
            )
        if query:
            pattern = _escape_like(query)
            statement = statement.where(
                or_(
                    model.contact_name.ilike(pattern, escape="\\"),
                    model.wa_id.ilike(pattern, escape="\\"),
                    model.last_message_preview.ilike(pattern, escape="\\"),
                    model.summary.ilike(pattern, escape="\\"),
                )
            )
        if filter == "unread":
            statement = statement.where(model.unread_count > 0)
        elif filter == "needs_reply":
            statement = statement.where(model.last_direction == Direction.INBOUND.value)
        elif filter == "awaiting_approval":
            message = WhatsAppMessageRecord
            statement = statement.where(
                exists().where(
                    message.tenant_id == model.tenant_id,
                    message.workspace_id == model.workspace_id,
                    message.conversation_id == model.id,
                    message.status == MessageStatus.AWAITING_APPROVAL.value,
                )
            )
        records = self.session.scalars(statement.order_by(model.last_message_at.desc(), model.id.desc()).limit(limit)).all()
        return [_conversation(record) for record in records]

    # -- messages ------------------------------------------------------------------------

    @staticmethod
    def _copy_message(message: Message, record: WhatsAppMessageRecord) -> None:
        for name in _MESSAGE_FIELDS:
            setattr(record, name, getattr(message, name))
        record.direction = message.direction.value
        record.status = message.status.value
        record.media = message.media.model_dump(mode="json") if message.media else None
        record.template = message.template.model_dump(mode="json") if message.template else None

    def add_message(self, message: Message) -> bool:
        record = WhatsAppMessageRecord(id=message.id, tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
        self._copy_message(message, record)
        try:
            # A savepoint: a duplicate wamid (a webhook retry) leaves the rest of the batch intact.
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
        except IntegrityError as exc:
            if WAMID_UNIQUE in str(exc.orig):
                return False
            raise
        return True

    def _message_record(self, *conditions: Any, lock: bool = False) -> WhatsAppMessageRecord | None:
        statement = select(WhatsAppMessageRecord).where(*self._scoped(WhatsAppMessageRecord), *conditions)
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def save_message(self, message: Message) -> Message:
        record = self._message_record(WhatsAppMessageRecord.id == message.id, lock=True)
        if record is None:
            raise KeyError(message.id)
        self._copy_message(message, record)
        self.session.flush()
        return message

    def get_message(self, message_id: str) -> Message | None:
        record = self._message_record(WhatsAppMessageRecord.id == message_id)
        return _message(record) if record is not None else None

    def message_by_wamid(self, wamid: str) -> Message | None:
        record = self._message_record(WhatsAppMessageRecord.wamid == wamid, lock=True)
        return _message(record) if record is not None else None

    def list_messages(self, conversation_id: str, *, limit: int, before: Message | None) -> list[Message]:
        model = WhatsAppMessageRecord
        statement = select(model).where(*self._scoped(model), model.conversation_id == conversation_id)
        if before is not None:
            statement = statement.where(
                or_(model.timestamp < before.timestamp, and_(model.timestamp == before.timestamp, model.id < before.id))
            )
        records = self.session.scalars(statement.order_by(model.timestamp.desc(), model.id.desc()).limit(limit)).all()
        return [_message(record) for record in records]

    def awaiting(self, conversation_id: str) -> list[Message]:
        records = self.session.scalars(
            select(WhatsAppMessageRecord).where(
                *self._scoped(WhatsAppMessageRecord),
                WhatsAppMessageRecord.conversation_id == conversation_id,
                WhatsAppMessageRecord.status == MessageStatus.AWAITING_APPROVAL.value,
            )
        ).all()
        return [_message(record) for record in records]


# -- webhook discovery (as anum_maintenance) -----------------------------------------------

_ROUTES = text(
    "select phone_number_id, tenant_id, workspace_id from whatsapp_accounts where phone_number_id in :ids"
).bindparams(bindparam("ids", expanding=True))
_VERIFY_TOKEN = text("select 1 from whatsapp_accounts where verify_token_hash = :hash limit 1")


def discover_routes(phone_number_ids: set[str]) -> dict[str, tuple[str, str]]:
    rows = discover(default_session_factory, _ROUTES, {"ids": sorted(phone_number_ids)})
    return {row.phone_number_id: (row.tenant_id, row.workspace_id) for row in rows}


def discover_verify_token(token_hash: str) -> bool:
    return bool(discover(default_session_factory, _VERIFY_TOKEN, {"hash": token_hash}))
