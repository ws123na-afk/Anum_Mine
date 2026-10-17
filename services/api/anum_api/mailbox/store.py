"""Mailboxes and outgoing messages of the Email Center (migration ``0060_email_center``).

Two tables, both private to the person who connected the mailbox: forced RLS on tenant and
workspace plus a restrictive ``owner_user_id = anum.user_id`` policy, and every query here
repeats the owner. Mail itself stays on the mail server: ANUM stores only the mailbox (with
its password as Fernet ciphertext) and the messages a person writes in ANUM (drafts and
sends, with their idempotency key and Message-ID).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext, utc_now
from ..scoped_store import open_scoped_store


class AccountStatus(StrEnum):
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    OFFLINE = "offline"
    ERROR = "error"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    # The connection broke while the server was taking the message: never retried by itself.
    UNKNOWN = "unknown"


EDITABLE = frozenset({DraftStatus.DRAFT, DraftStatus.FAILED, DraftStatus.PENDING_APPROVAL})
DraftKind = Literal["new", "reply", "reply_all", "forward"]


class MailAccount(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    address: str
    display_name: str | None = None
    password_enc: str
    status: AccountStatus = AccountStatus.CONNECTED
    last_error: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailDraft(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    kind: DraftKind = "new"
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    in_reply_to: str | None = None
    references: str | None = None
    source_folder: str | None = None
    source_uid: int | None = None
    message_id: str
    idempotency_key: str | None = None
    status: DraftStatus = DraftStatus.DRAFT
    approval_id: str | None = None
    task_id: str | None = None
    error: str | None = None
    saved_to_sent: bool | None = None
    sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailStoreProtocol(Protocol):
    def list_accounts(self) -> list[MailAccount]: ...
    def get_account(self, account_id: str) -> MailAccount | None: ...
    def save_account(self, account: MailAccount) -> MailAccount: ...
    def delete_account(self, account_id: str) -> bool: ...
    def list_drafts(self, account_id: str | None, limit: int) -> list[MailDraft]: ...
    def get_draft(self, draft_id: str) -> MailDraft | None: ...
    def find_by_key(self, account_id: str, key: str) -> MailDraft | None: ...
    def save_draft(self, draft: MailDraft) -> MailDraft: ...
    def delete_draft(self, draft_id: str) -> bool: ...
    def claim_send(self, draft_id: str) -> bool: ...
    def count_sends_since(self, account_id: str, since: datetime) -> int: ...


class InMemoryMailBackend:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.accounts: dict[str, MailAccount] = {}
        self.drafts: dict[str, MailDraft] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.accounts.clear()
            self.drafts.clear()


class MemoryMailScope:
    """The memory store behaves like the row policies: own tenant, workspace and owner only."""

    def __init__(self, backend: InMemoryMailBackend, context: TenantContext) -> None:
        self.b = backend
        self.scope = (context.tenant_id, context.workspace_id, context.user_id)

    def _mine(self, item: MailAccount | MailDraft) -> bool:
        return (item.tenant_id, item.workspace_id, item.owner_user_id) == self.scope

    def list_accounts(self) -> list[MailAccount]:
        with self.b.lock:
            return sorted((a.model_copy() for a in self.b.accounts.values() if self._mine(a)), key=lambda a: a.created_at)

    def get_account(self, account_id: str) -> MailAccount | None:
        with self.b.lock:
            found = self.b.accounts.get(account_id)
            return found.model_copy() if found and self._mine(found) else None

    def save_account(self, account: MailAccount) -> MailAccount:
        if not self._mine(account):
            raise PermissionError("row outside the caller's scope")
        with self.b.lock:
            clash = next(
                (a for a in self.b.accounts.values() if a.id != account.id and (a.tenant_id, a.workspace_id, a.address.lower()) == (account.tenant_id, account.workspace_id, account.address.lower())),
                None,
            )
            if clash is not None:
                raise ValueError("mailbox already connected in this workspace")
            self.b.accounts[account.id] = account.model_copy()
        return account

    def delete_account(self, account_id: str) -> bool:
        with self.b.lock:
            found = self.b.accounts.get(account_id)
            if not found or not self._mine(found):
                return False
            del self.b.accounts[account_id]
            for draft_id in [d.id for d in self.b.drafts.values() if d.account_id == account_id]:
                del self.b.drafts[draft_id]
            return True

    def list_drafts(self, account_id: str | None, limit: int) -> list[MailDraft]:
        with self.b.lock:
            items = [d.model_copy() for d in self.b.drafts.values() if self._mine(d) and (account_id is None or d.account_id == account_id)]
        return sorted(items, key=lambda d: (d.updated_at, d.id), reverse=True)[:limit]

    def get_draft(self, draft_id: str) -> MailDraft | None:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            return found.model_copy() if found and self._mine(found) else None

    def find_by_key(self, account_id: str, key: str) -> MailDraft | None:
        with self.b.lock:
            found = next((d for d in self.b.drafts.values() if self._mine(d) and d.account_id == account_id and d.idempotency_key == key), None)
            return found.model_copy() if found else None

    def save_draft(self, draft: MailDraft) -> MailDraft:
        if not self._mine(draft):
            raise PermissionError("row outside the caller's scope")
        with self.b.lock:
            clash = next(
                (d for d in self.b.drafts.values() if d.id != draft.id and d.account_id == draft.account_id and draft.idempotency_key and d.idempotency_key == draft.idempotency_key),
                None,
            )
            if clash is not None:
                raise ValueError("idempotency key already used")
            self.b.drafts[draft.id] = draft.model_copy()
        return draft

    def delete_draft(self, draft_id: str) -> bool:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or not self._mine(found):
                return False
            del self.b.drafts[draft_id]
            return True

    def claim_send(self, draft_id: str) -> bool:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or not self._mine(found) or found.status != DraftStatus.PENDING_APPROVAL:
                return False
            found.status = DraftStatus.SENDING
            found.updated_at = utc_now()
            return True

    def count_sends_since(self, account_id: str, since: datetime) -> int:
        with self.b.lock:
            return sum(
                1
                for d in self.b.drafts.values()
                if self._mine(d) and d.account_id == account_id and d.status in {DraftStatus.SENT, DraftStatus.SENDING, DraftStatus.UNKNOWN}
                and d.updated_at >= since
            )


mail_backend = InMemoryMailBackend()


@contextmanager
def open_mail_store(context: TenantContext) -> Iterator[MailStoreProtocol]:
    """One unit of work as the caller (tenant, workspace and user RLS with PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.mailbox_repository import SqlAlchemyMailStore

        return SqlAlchemyMailStore(session, context)

    with open_scoped_store(context, MemoryMailScope(mail_backend, context), sql_store, user_scoped=True) as store:
        yield store
