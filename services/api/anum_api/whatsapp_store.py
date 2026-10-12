"""WhatsApp inbox storage (docs/whatsapp.md#storage).

One WhatsApp Cloud API account per workspace, and the workspace's contacts,
conversations and messages. ``InMemoryWhatsAppStore`` serves
``ANUM_REPOSITORY_BACKEND=memory`` (local and tests); with PostgreSQL the rows live in
forced-RLS tables (migration ``0033_whatsapp``, ``anum_api.db.whatsapp_repository``)
with the access token and app secret encrypted with ``ANUM_SECRETS_KEY``.

Every method works inside one tenant and workspace. The only cross-tenant reads are the
webhook's two discovery lookups (which workspace owns a phone number id; does any
workspace use a verify token), done as ``anum_maintenance`` in PostgreSQL
(docs/multi-tenancy.md#maintenance-role).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, SecretStr

from .schemas import TenantContext
from .scoped_store import open_scoped_store

SERVICE_WINDOW = timedelta(hours=24)
PREVIEW_CHARS = 160


class AccountStatus(StrEnum):
    UNVERIFIED = "unverified"
    CONNECTED = "connected"
    ERROR = "error"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    AWAITING_APPROVAL = "awaiting_approval"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    NOT_SENT = "not_sent"


# Delivery statuses only move forward (a replayed or late webhook cannot move one back).
STATUS_RANK = {
    MessageStatus.AWAITING_APPROVAL: 0,
    MessageStatus.SENDING: 1,
    MessageStatus.SENT: 2,
    MessageStatus.DELIVERED: 3,
    MessageStatus.READ: 4,
}


class Category(StrEnum):
    ORDER_STATUS = "order_status"
    PRICE_STOCK = "price_stock"
    COMPLAINT = "complaint"
    SUPPLIER = "supplier"
    OTHER = "other"


ConversationFilter = Literal["all", "unread", "needs_reply", "awaiting_approval"]


def verify_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def without_secrets() -> dict[str, None]:
    """``model_copy`` update that drops the decrypted credentials from an account."""
    return dict.fromkeys(("access_token", "app_secret"))


def secret_hint(value: str | None) -> str | None:
    return f"...{value[-4:]}" if value and len(value) >= 8 else None


class WhatsAppAccount(BaseModel):
    tenant_id: str
    workspace_id: str
    phone_number_id: str
    business_account_id: str
    display_phone_number: str | None = None
    verified_name: str | None = None
    quality_rating: str | None = None
    # Present only when the store was asked for secrets; never serialized to clients.
    access_token: SecretStr | None = None
    app_secret: SecretStr | None = None
    access_token_hint: str | None = None
    verify_token_hash: str
    owner_test_number: str | None = None
    auto_send_templates: list[str] = Field(default_factory=list)
    triage_enabled: bool = True
    # History retention in days (docs/whatsapp.md#retention): None follows the deployment
    # default (ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS), 0 keeps messages until deleted.
    message_retention_days: int | None = None
    status: AccountStatus = AccountStatus.UNVERIFIED
    last_error: str | None = None
    last_checked_at: datetime | None = None
    last_webhook_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    updated_by: str


class Contact(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    wa_id: str
    profile_name: str | None = None
    created_at: datetime
    updated_at: datetime


class Suggestion(BaseModel):
    """An AI (or rules) suggestion for a conversation. ``text`` came from a model that read
    untrusted customer messages: it is a draft for a person to check, never sent by itself."""

    text: str
    category: Category
    summary: str
    language: str | None = None
    source: Literal["model", "rules"]
    provider: str | None = None
    model: str | None = None
    generated_at: datetime
    generated_by: str
    # How many customer messages the model read (all labelled as untrusted data).
    untrusted_messages: int = 0


class Conversation(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    contact_id: str
    wa_id: str
    contact_name: str | None = None
    last_message_at: datetime
    last_message_preview: str = ""
    last_direction: Direction = Direction.INBOUND
    last_inbound_at: datetime | None = None
    unread_count: int = 0
    category: Category | None = None
    category_source: Literal["model", "rules"] | None = None
    summary: str | None = None
    suggestion: Suggestion | None = None
    needs_triage: bool = False
    triaged_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    def service_window_closes_at(self) -> datetime | None:
        return self.last_inbound_at + SERVICE_WINDOW if self.last_inbound_at else None

    def is_window_open(self, now: datetime) -> bool:
        closes = self.service_window_closes_at()
        return closes is not None and now < closes


class MediaInfo(BaseModel):
    id: str
    mime_type: str | None = None
    sha256: str | None = None
    filename: str | None = None
    caption: str | None = None
    voice: bool = False


class TemplateUse(BaseModel):
    name: str
    language: str
    parameters: list[str] = Field(default_factory=list)


class Message(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    conversation_id: str
    wamid: str | None = None
    direction: Direction
    kind: str
    body: str = ""
    media: MediaInfo | None = None
    template: TemplateUse | None = None
    status: MessageStatus
    error: str | None = None
    reply_to: str | None = None
    sent_by: str | None = None
    approval_id: str | None = None
    task_id: str | None = None
    timestamp: datetime
    created_at: datetime
    updated_at: datetime


def preview(message: Message) -> str:
    if message.body:
        text = " ".join(message.body.split())
    elif message.media is not None:
        text = f"[{message.kind}]" + (f" {message.media.caption}" if message.media.caption else "")
    else:
        text = f"[{message.kind}]"
    return text[:PREVIEW_CHARS]


class WhatsAppStoreProtocol(Protocol):
    def get_account(self, *, include_secrets: bool = False) -> WhatsAppAccount | None: ...
    def save_account(self, account: WhatsAppAccount, *, access_token: str | None, app_secret: str | None) -> WhatsAppAccount:
        """Insert or replace this workspace's account. ``None`` secrets keep the saved ones.
        Raises ``PhoneNumberTakenError`` when another workspace owns the phone number id."""
        ...
    def delete_account(self) -> bool: ...
    def upsert_contact(self, wa_id: str, profile_name: str | None, now: datetime, new_id: str) -> Contact: ...
    def conversation_for(self, wa_id: str) -> Conversation | None: ...
    def get_conversation(self, conversation_id: str) -> Conversation | None: ...
    def add_conversation(self, conversation: Conversation) -> Conversation: ...
    def save_conversation(self, conversation: Conversation) -> Conversation: ...
    def list_conversations(
        self, *, limit: int, before: Conversation | None, query: str | None, filter: ConversationFilter
    ) -> list[Conversation]: ...
    def add_message(self, message: Message) -> bool:
        """Store ``message``; False (nothing stored) when its ``wamid`` is already stored."""
        ...
    def save_message(self, message: Message) -> Message: ...
    def get_message(self, message_id: str) -> Message | None: ...
    def message_by_wamid(self, wamid: str) -> Message | None: ...
    def list_messages(self, conversation_id: str, *, limit: int, before: Message | None) -> list[Message]: ...
    def awaiting(self, conversation_id: str) -> list[Message]: ...


class PhoneNumberTakenError(Exception):
    """The phone number id is already connected to another workspace."""


def _conversation_key(item: Conversation) -> tuple[datetime, str]:
    return (item.last_message_at, item.id)


def _message_key(item: Message) -> tuple[datetime, str]:
    return (item.timestamp, item.id)


def _matches(item: Conversation, query: str | None) -> bool:
    if not query:
        return True
    needle = query.lower()
    return any(needle in (value or "").lower() for value in (item.contact_name, item.wa_id, item.last_message_preview, item.summary))


class InMemoryWhatsAppStore:
    """``ANUM_REPOSITORY_BACKEND=memory``: everything is lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._accounts: dict[tuple[str, str], tuple[WhatsAppAccount, str | None, str | None]] = {}
        self._contacts: dict[str, Contact] = {}
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, Message] = {}

    def clear(self) -> None:
        with self._lock:
            self._accounts.clear()
            self._contacts.clear()
            self._conversations.clear()
            self._messages.clear()

    def scoped(self, context: TenantContext) -> _ScopedMemoryStore:
        return _ScopedMemoryStore(self, context)

    # Discovery for the webhook (no tenant context: it has to find the tenant).
    def route(self, phone_number_ids: set[str]) -> dict[str, tuple[str, str]]:
        with self._lock:
            return {
                account.phone_number_id: scope
                for scope, (account, _, _) in self._accounts.items()
                if account.phone_number_id in phone_number_ids
            }

    def verify_token_known(self, token_hash: str) -> bool:
        with self._lock:
            return any(account.verify_token_hash == token_hash for account, _, _ in self._accounts.values())


class _ScopedMemoryStore:
    def __init__(self, root: InMemoryWhatsAppStore, context: TenantContext) -> None:
        self.root = root
        self.scope = (context.tenant_id, context.workspace_id)

    def _mine(self, item: Any) -> bool:
        return (item.tenant_id, item.workspace_id) == self.scope

    def get_account(self, *, include_secrets: bool = False) -> WhatsAppAccount | None:
        with self.root._lock:
            found = self.root._accounts.get(self.scope)
        if found is None:
            return None
        account, token, secret = found
        copy = account.model_copy(deep=True)
        if include_secrets:
            copy.access_token = SecretStr(token) if token else None
            copy.app_secret = SecretStr(secret) if secret else None
        return copy

    def save_account(self, account: WhatsAppAccount, *, access_token: str | None, app_secret: str | None) -> WhatsAppAccount:
        with self.root._lock:
            for scope, (other, _, _) in self.root._accounts.items():
                if scope != self.scope and other.phone_number_id == account.phone_number_id:
                    raise PhoneNumberTakenError(account.phone_number_id)
            previous = self.root._accounts.get(self.scope)
            token = access_token if access_token is not None else (previous[1] if previous else None)
            secret = app_secret if app_secret is not None else (previous[2] if previous else None)
            stored = account.model_copy(
                update={**without_secrets(), "access_token_hint": secret_hint(token)}, deep=True
            )
            self.root._accounts[self.scope] = (stored, token, secret)
        return stored.model_copy(deep=True)

    def delete_account(self) -> bool:
        with self.root._lock:
            return self.root._accounts.pop(self.scope, None) is not None

    def upsert_contact(self, wa_id: str, profile_name: str | None, now: datetime, new_id: str) -> Contact:
        with self.root._lock:
            for contact in self.root._contacts.values():
                if self._mine(contact) and contact.wa_id == wa_id:
                    if profile_name and profile_name != contact.profile_name:
                        contact.profile_name = profile_name
                        contact.updated_at = now
                    return contact.model_copy()
            contact = Contact(
                id=new_id, tenant_id=self.scope[0], workspace_id=self.scope[1], wa_id=wa_id,
                profile_name=profile_name, created_at=now, updated_at=now,
            )
            self.root._contacts[contact.id] = contact
            return contact.model_copy()

    def conversation_for(self, wa_id: str) -> Conversation | None:
        with self.root._lock:
            for item in self.root._conversations.values():
                if self._mine(item) and item.wa_id == wa_id:
                    return item.model_copy(deep=True)
        return None

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self.root._lock:
            item = self.root._conversations.get(conversation_id)
        return item.model_copy(deep=True) if item is not None and self._mine(item) else None

    def add_conversation(self, conversation: Conversation) -> Conversation:
        with self.root._lock:
            self.root._conversations[conversation.id] = conversation.model_copy(deep=True)
        return conversation

    def save_conversation(self, conversation: Conversation) -> Conversation:
        with self.root._lock:
            current = self.root._conversations.get(conversation.id)
            if current is None or not self._mine(current):
                raise KeyError(conversation.id)
            self.root._conversations[conversation.id] = conversation.model_copy(deep=True)
        return conversation

    def list_conversations(
        self, *, limit: int, before: Conversation | None, query: str | None, filter: ConversationFilter
    ) -> list[Conversation]:
        with self.root._lock:
            items = [item for item in self.root._conversations.values() if self._mine(item)]
            awaiting = {
                message.conversation_id
                for message in self.root._messages.values()
                if self._mine(message) and message.status == MessageStatus.AWAITING_APPROVAL
            }
        if before is not None:
            items = [item for item in items if _conversation_key(item) < _conversation_key(before)]
        items = [item for item in items if _matches(item, query)]
        if filter == "unread":
            items = [item for item in items if item.unread_count > 0]
        elif filter == "needs_reply":
            items = [item for item in items if item.last_direction == Direction.INBOUND]
        elif filter == "awaiting_approval":
            items = [item for item in items if item.id in awaiting]
        items.sort(key=_conversation_key, reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def add_message(self, message: Message) -> bool:
        with self.root._lock:
            if message.wamid and any(
                self._mine(item) and item.wamid == message.wamid for item in self.root._messages.values()
            ):
                return False
            self.root._messages[message.id] = message.model_copy(deep=True)
        return True

    def save_message(self, message: Message) -> Message:
        with self.root._lock:
            current = self.root._messages.get(message.id)
            if current is None or not self._mine(current):
                raise KeyError(message.id)
            self.root._messages[message.id] = message.model_copy(deep=True)
        return message

    def get_message(self, message_id: str) -> Message | None:
        with self.root._lock:
            item = self.root._messages.get(message_id)
        return item.model_copy(deep=True) if item is not None and self._mine(item) else None

    def message_by_wamid(self, wamid: str) -> Message | None:
        with self.root._lock:
            for item in self.root._messages.values():
                if self._mine(item) and item.wamid == wamid:
                    return item.model_copy(deep=True)
        return None

    def list_messages(self, conversation_id: str, *, limit: int, before: Message | None) -> list[Message]:
        with self.root._lock:
            items = [
                item for item in self.root._messages.values()
                if self._mine(item) and item.conversation_id == conversation_id
            ]
        if before is not None:
            items = [item for item in items if _message_key(item) < _message_key(before)]
        items.sort(key=_message_key, reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def awaiting(self, conversation_id: str) -> list[Message]:
        with self.root._lock:
            return [
                item.model_copy(deep=True)
                for item in self.root._messages.values()
                if self._mine(item) and item.conversation_id == conversation_id and item.status == MessageStatus.AWAITING_APPROVAL
            ]


whatsapp_store = InMemoryWhatsAppStore()


@contextmanager
def open_whatsapp_store(context: TenantContext) -> Iterator[WhatsAppStoreProtocol]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.whatsapp_repository import SqlAlchemyWhatsAppStore
        from .secret_box import secret_cipher

        return SqlAlchemyWhatsAppStore(session, context, secret_cipher())

    with open_scoped_store(context, whatsapp_store.scoped(context), sql_store) as store:
        yield store


def route_phone_numbers(phone_number_ids: set[str]) -> dict[str, tuple[str, str]]:
    """Which tenant and workspace own each phone number id (webhook routing).

    PostgreSQL: one discovery query as ``anum_maintenance``, which may read only the
    phone number id, tenant and workspace of ``whatsapp_accounts`` (migration 0026).
    """
    from .settings import settings

    if not phone_number_ids:
        return {}
    if settings.repository_backend == "memory":
        return whatsapp_store.route(phone_number_ids)
    from .db.whatsapp_repository import discover_routes

    return discover_routes(phone_number_ids)


def verify_token_known(token: str) -> bool:
    """Whether any workspace saved this webhook verify token (only its hash is stored)."""
    from .settings import settings

    token_hash = verify_token_hash(token)
    if settings.repository_backend == "memory":
        return whatsapp_store.verify_token_known(token_hash)
    from .db.whatsapp_repository import discover_verify_token

    return discover_verify_token(token_hash)
