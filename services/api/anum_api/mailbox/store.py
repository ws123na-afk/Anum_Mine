"""Mailboxes and outgoing messages of the Email Center (migration ``0060_email_center``).

Three tables, all private to the person who connected the mailbox: forced RLS on tenant and
workspace plus a restrictive ``owner_user_id = anum.user_id`` policy, and every query here
repeats the owner. Mail itself stays on the mail server: ANUM stores only the mailbox (with
its password as Fernet ciphertext), the messages a person writes in ANUM (drafts and
sends, with their idempotency key and Message-ID, and a time for a scheduled send) and the
files attached to them (``mail_attachments``, migration ``0062_mail_outgoing``; the bytes are
in object storage).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
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
    # Approved, waiting for its ``send_at``; the scheduler sends it then (docs/email.md#scheduled-send).
    SCHEDULED = "scheduled"


# Editing any of these withdraws a pending approval or cancels the schedule (back to a draft).
EDITABLE = frozenset({DraftStatus.DRAFT, DraftStatus.FAILED, DraftStatus.PENDING_APPROVAL, DraftStatus.SCHEDULED})
# A claimed send whose lease ran out may have reached the server: it becomes ``unknown``.
SEND_LEASE = timedelta(minutes=10)
INTERRUPTED = (
    "The send was interrupted (the ANUM server restarted) and may have reached the mail server. "
    "Check Sent before sending it again."
)
NOT_APPROVED_IN_TIME = "Not sent: it was not approved before its scheduled time. Pick a new time and send it again."
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
    # Scheduled send: when to send (UTC), the hash of the approved send call, and the lease of a
    # claimed send (past it, the send counts as interrupted and becomes ``unknown``).
    send_at: datetime | None = None
    approved_hash: str | None = None
    send_lease_until: datetime | None = None
    # Why some of a forwarded message's attachments were not carried over (shown in the composer).
    attachment_note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailAttachment(BaseModel):
    """A file attached to a message written in ANUM; checked and scanned before it is kept."""

    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    draft_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    # False when no malware scanner is installed on this server (docs/files.md#malware-scanning).
    scanned: bool = False
    created_at: datetime = Field(default_factory=utc_now)


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
    def claim_send(self, draft_id: str, lease_until: datetime) -> bool: ...
    def count_sends_since(self, account_id: str, since: datetime) -> int: ...
    def mark_scheduled(self, draft_id: str, approved_hash: str) -> bool: ...
    def reset_to_draft(self, draft_id: str, expected: DraftStatus) -> bool: ...
    def claim_scheduled(self, draft_id: str, now: datetime, lease_until: datetime) -> bool: ...
    def list_scheduled(self, account_id: str | None, limit: int) -> list[MailDraft]: ...
    def release_interrupted(self, draft_id: str, now: datetime) -> bool: ...
    def expire_unapproved(self, draft_id: str, before: datetime) -> MailDraft | None: ...
    def list_attachments(self, draft_id: str) -> list[MailAttachment]: ...
    def attachments_of_account(self, account_id: str | None) -> list[MailAttachment]: ...
    def get_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None: ...
    def save_attachment(self, attachment: MailAttachment) -> MailAttachment: ...
    def delete_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None: ...
    def pending_attachment_bytes(self) -> int: ...


class InMemoryMailBackend:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.accounts: dict[str, MailAccount] = {}
        self.drafts: dict[str, MailDraft] = {}
        self.attachments: dict[str, MailAttachment] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.accounts.clear()
            self.drafts.clear()
            self.attachments.clear()

    def due_work(self, now: datetime, unapproved_before: datetime, limit: int) -> list[tuple[str, str, str, str]]:
        """The scheduler's discovery in memory mode (PostgreSQL asks as ``anum_maintenance``)."""
        with self.lock:
            found = [
                (d.tenant_id, d.workspace_id, d.owner_user_id, d.id)
                for d in self.drafts.values()
                if (d.status == DraftStatus.SCHEDULED and d.send_at is not None and d.send_at <= now)
                or (d.status == DraftStatus.PENDING_APPROVAL and d.send_at is not None and d.send_at < unapproved_before)
                or (d.status == DraftStatus.SENDING and d.send_lease_until is not None and d.send_lease_until < now)
            ]
        return found[:limit]


class MemoryMailScope:
    """The memory store behaves like the row policies: own tenant, workspace and owner only."""

    def __init__(self, backend: InMemoryMailBackend, context: TenantContext) -> None:
        self.b = backend
        self.scope = (context.tenant_id, context.workspace_id, context.user_id)

    def _mine(self, item: MailAccount | MailDraft | MailAttachment) -> bool:
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
                self._drop_draft(draft_id)
            return True

    def _drop_draft(self, draft_id: str) -> None:
        del self.b.drafts[draft_id]
        for attachment_id in [a.id for a in self.b.attachments.values() if a.draft_id == draft_id]:
            del self.b.attachments[attachment_id]

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
            self._drop_draft(draft_id)
            return True

    def _move(self, draft_id: str, expected: DraftStatus, new: DraftStatus, **changes: object) -> MailDraft | None:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or not self._mine(found) or found.status != expected:
                return None
            found.status = new
            for key, value in changes.items():
                setattr(found, key, value)
            found.updated_at = utc_now()
            return found.model_copy()

    def claim_send(self, draft_id: str, lease_until: datetime) -> bool:
        return self._move(draft_id, DraftStatus.PENDING_APPROVAL, DraftStatus.SENDING, send_lease_until=lease_until) is not None

    def mark_scheduled(self, draft_id: str, approved_hash: str) -> bool:
        return self._move(draft_id, DraftStatus.PENDING_APPROVAL, DraftStatus.SCHEDULED, approved_hash=approved_hash, error=None) is not None

    def reset_to_draft(self, draft_id: str, expected: DraftStatus) -> bool:
        return self._move(draft_id, expected, DraftStatus.DRAFT, approved_hash=None) is not None

    def claim_scheduled(self, draft_id: str, now: datetime, lease_until: datetime) -> bool:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or found.send_at is None or found.send_at > now:
                return False
            return self._move(draft_id, DraftStatus.SCHEDULED, DraftStatus.SENDING, send_lease_until=lease_until) is not None

    def list_scheduled(self, account_id: str | None, limit: int) -> list[MailDraft]:
        with self.b.lock:
            items = [
                d.model_copy() for d in self.b.drafts.values()
                if self._mine(d) and d.send_at is not None and d.status in {DraftStatus.SCHEDULED, DraftStatus.PENDING_APPROVAL}
                and (account_id is None or d.account_id == account_id)
            ]
        return sorted(items, key=lambda d: (d.send_at or d.updated_at, d.id))[:limit]

    def release_interrupted(self, draft_id: str, now: datetime) -> bool:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or found.send_lease_until is None or found.send_lease_until >= now:
                return False
            return self._move(draft_id, DraftStatus.SENDING, DraftStatus.UNKNOWN, error=INTERRUPTED, send_lease_until=None) is not None

    def expire_unapproved(self, draft_id: str, before: datetime) -> MailDraft | None:
        with self.b.lock:
            found = self.b.drafts.get(draft_id)
            if not found or found.send_at is None or found.send_at >= before:
                return None
            previous = found.model_copy()
            moved = self._move(draft_id, DraftStatus.PENDING_APPROVAL, DraftStatus.DRAFT, error=NOT_APPROVED_IN_TIME, approval_id=None, task_id=None, idempotency_key=None)
            return previous if moved is not None else None

    def list_attachments(self, draft_id: str) -> list[MailAttachment]:
        with self.b.lock:
            items = [a.model_copy() for a in self.b.attachments.values() if self._mine(a) and a.draft_id == draft_id]
        return sorted(items, key=lambda a: (a.created_at, a.id))

    def attachments_of_account(self, account_id: str | None) -> list[MailAttachment]:
        with self.b.lock:
            drafts = {d.id for d in self.b.drafts.values() if self._mine(d) and (account_id is None or d.account_id == account_id)}
            return [a.model_copy() for a in self.b.attachments.values() if self._mine(a) and a.draft_id in drafts]

    def get_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None:
        with self.b.lock:
            found = self.b.attachments.get(attachment_id)
            return found.model_copy() if found and self._mine(found) and found.draft_id == draft_id else None

    def save_attachment(self, attachment: MailAttachment) -> MailAttachment:
        if not self._mine(attachment):
            raise PermissionError("row outside the caller's scope")
        with self.b.lock:
            draft = self.b.drafts.get(attachment.draft_id)
            if draft is None or not self._mine(draft):
                raise LookupError("draft not found")
            self.b.attachments[attachment.id] = attachment.model_copy()
        return attachment

    def delete_attachment(self, draft_id: str, attachment_id: str) -> MailAttachment | None:
        with self.b.lock:
            found = self.b.attachments.get(attachment_id)
            if not found or not self._mine(found) or found.draft_id != draft_id:
                return None
            del self.b.attachments[attachment_id]
            return found

    def pending_attachment_bytes(self) -> int:
        with self.b.lock:
            unsent = {d.id for d in self.b.drafts.values() if self._mine(d) and d.status != DraftStatus.SENT}
            return sum(a.size_bytes for a in self.b.attachments.values() if self._mine(a) and a.draft_id in unsent)

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
