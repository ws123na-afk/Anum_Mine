"""Mailboxes, outgoing messages, shares, identities and contacts of the Email Center.

Migrations ``0060_email_center``, ``0062_mail_outgoing`` and ``0067_mail_sharing_contacts``.
Every table has forced RLS on tenant and workspace plus restrictive owner policies, and every
query here repeats the scope:

* a mailbox is visible to its owner and to people the owner granted access
  (``mail_account_grants``); only the owner changes it;
* a message written in ANUM (``mail_drafts``) and its attachments (``mail_attachments``) are
  private to the person who wrote it;
* sender identities are visible to the owner and to people granted ``send``;
* contacts are private to their owner.

Mail itself stays on the mail server: ANUM stores only the mailbox (with its password as
Fernet ciphertext), the messages a person writes in ANUM (drafts and sends, with their
idempotency key and Message-ID, and a time for a scheduled send), the files attached to them
(the bytes are in object storage), the shares, the identities and the address book.
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
# What a person may do with a mailbox: their own, or shared with them to send or only to read.
Access = Literal["owner", "send", "read"]
GrantAccess = Literal["read", "send"]
ACCESS_RANK = {"read": 1, "send": 2, "owner": 3}


def allows(access: Access | None, needed: Access) -> bool:
    return access is not None and ACCESS_RANK[access] >= ACCESS_RANK[needed]


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
    identity_id: str | None = None
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


class MailGrant(BaseModel):
    """A mailbox shared by its owner with another member of the workspace."""

    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    grantee_user_id: str
    access: GrantAccess = "read"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailIdentity(BaseModel):
    """An address and name a mailbox sends as (an alias on the mail server), with a signature."""

    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    address: str
    display_name: str | None = None
    signature: str = ""
    is_default: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailContact(BaseModel):
    """One entry of a person's own address book (the email is stored in lower case)."""

    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    name: str = ""
    email: str
    company: str = ""
    phone: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def contact_sort_key(contact: MailContact) -> tuple[str, str]:
    """Contacts list by name (or the address when there is no name), then id: the paging key."""
    return ((contact.name or contact.email).lower(), contact.id)


def contact_matches(contact: MailContact, query: str) -> bool:
    needle = query.lower()
    return any(needle in value.lower() for value in (contact.name, contact.email, contact.company))


class MailStoreProtocol(Protocol):
    def list_accounts(self) -> list[MailAccount]: ...
    def get_account(self, account_id: str) -> MailAccount | None: ...
    def list_shared_accounts(self) -> list[tuple[MailAccount, MailGrant]]: ...
    def get_accessible_account(self, account_id: str) -> tuple[MailAccount, Access] | None: ...
    def list_grants(self, account_id: str) -> list[MailGrant]: ...
    def get_grant(self, grant_id: str) -> MailGrant | None: ...
    def save_grant(self, grant: MailGrant) -> MailGrant: ...
    def delete_grant(self, grant_id: str) -> bool: ...
    def list_identities(self, account_id: str) -> list[MailIdentity]: ...
    def get_identity(self, identity_id: str) -> MailIdentity | None: ...
    def save_identity(self, identity: MailIdentity) -> MailIdentity: ...
    def delete_identity(self, identity_id: str) -> bool: ...
    def list_contacts(self, query: str | None, after: tuple[str, str] | None, limit: int) -> tuple[list[MailContact], int]: ...
    def get_contact(self, contact_id: str) -> MailContact | None: ...
    def find_contact(self, email: str) -> MailContact | None: ...
    def save_contact(self, contact: MailContact) -> MailContact: ...
    def delete_contact(self, contact_id: str) -> bool: ...
    def count_contacts(self) -> int: ...
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
        self.grants: dict[str, MailGrant] = {}
        self.identities: dict[str, MailIdentity] = {}
        self.contacts: dict[str, MailContact] = {}
        self.attachments: dict[str, MailAttachment] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.accounts.clear()
            self.drafts.clear()
            self.grants.clear()
            self.identities.clear()
            self.contacts.clear()
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

    def _mine(self, item: MailAccount | MailDraft | MailAttachment | MailGrant | MailIdentity | MailContact) -> bool:
        return (item.tenant_id, item.workspace_id, item.owner_user_id) == self.scope

    def _in_workspace(self, item: MailAccount | MailGrant | MailIdentity) -> bool:
        return (item.tenant_id, item.workspace_id) == self.scope[:2]

    def _my_grant(self, account_id: str) -> MailGrant | None:
        return next(
            (g for g in self.b.grants.values() if self._in_workspace(g) and g.account_id == account_id and g.grantee_user_id == self.scope[2]),
            None,
        )

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
            # Like the foreign keys' ON DELETE CASCADE: everyone's drafts (with their attachments),
            # the shares, the identities.
            for draft_id in [d.id for d in self.b.drafts.values() if d.account_id == account_id]:
                self._drop_draft(draft_id)
            for table in (self.b.grants, self.b.identities):
                for key in [k for k, v in table.items() if v.account_id == account_id]:  # type: ignore[attr-defined]
                    del table[key]
            return True

    def _drop_draft(self, draft_id: str) -> None:
        del self.b.drafts[draft_id]
        for attachment_id in [a.id for a in self.b.attachments.values() if a.draft_id == draft_id]:
            del self.b.attachments[attachment_id]

    # -- shared mailboxes -------------------------------------------------------------------

    def list_shared_accounts(self) -> list[tuple[MailAccount, MailGrant]]:
        with self.b.lock:
            found = [
                (self.b.accounts[g.account_id].model_copy(), g.model_copy())
                for g in self.b.grants.values()
                if self._in_workspace(g) and g.grantee_user_id == self.scope[2] and g.account_id in self.b.accounts
            ]
        return sorted(found, key=lambda pair: pair[0].created_at)

    def get_accessible_account(self, account_id: str) -> tuple[MailAccount, Access] | None:
        with self.b.lock:
            found = self.b.accounts.get(account_id)
            if found is None or not self._in_workspace(found):
                return None
            if found.owner_user_id == self.scope[2]:
                return found.model_copy(), "owner"
            grant = self._my_grant(account_id)
            return (found.model_copy(), grant.access) if grant else None

    def list_grants(self, account_id: str) -> list[MailGrant]:
        with self.b.lock:
            items = [g.model_copy() for g in self.b.grants.values() if self._mine(g) and g.account_id == account_id]
        return sorted(items, key=lambda g: (g.created_at, g.id))

    def get_grant(self, grant_id: str) -> MailGrant | None:
        with self.b.lock:
            found = self.b.grants.get(grant_id)
            if found is None or not self._in_workspace(found) or self.scope[2] not in {found.owner_user_id, found.grantee_user_id}:
                return None
            return found.model_copy()

    def save_grant(self, grant: MailGrant) -> MailGrant:
        with self.b.lock:
            account = self.b.accounts.get(grant.account_id)
            # The policy's check and the foreign key on (tenant, workspace, account, owner).
            if not self._mine(grant) or account is None or account.owner_user_id != grant.owner_user_id or not self._in_workspace(account):
                raise PermissionError("row outside the caller's scope")
            if grant.grantee_user_id == grant.owner_user_id:
                raise ValueError("a mailbox cannot be shared with its owner")
            clash = next((g for g in self.b.grants.values() if g.id != grant.id and g.account_id == grant.account_id and g.grantee_user_id == grant.grantee_user_id), None)
            if clash is not None:
                raise ValueError("already shared with this person")
            self.b.grants[grant.id] = grant.model_copy()
        return grant

    def delete_grant(self, grant_id: str) -> bool:
        with self.b.lock:
            found = self.b.grants.get(grant_id)
            if found is None or not self._in_workspace(found) or self.scope[2] not in {found.owner_user_id, found.grantee_user_id}:
                return False
            del self.b.grants[grant_id]
            return True

    # -- identities -------------------------------------------------------------------------

    def _sees_identity(self, identity: MailIdentity) -> bool:
        if self._mine(identity):
            return True
        grant = self._my_grant(identity.account_id)
        return self._in_workspace(identity) and grant is not None and grant.access == "send"

    def list_identities(self, account_id: str) -> list[MailIdentity]:
        with self.b.lock:
            items = [i.model_copy() for i in self.b.identities.values() if i.account_id == account_id and self._sees_identity(i)]
        return sorted(items, key=lambda i: (not i.is_default, i.address, i.id))

    def get_identity(self, identity_id: str) -> MailIdentity | None:
        with self.b.lock:
            found = self.b.identities.get(identity_id)
            return found.model_copy() if found and self._sees_identity(found) else None

    def save_identity(self, identity: MailIdentity) -> MailIdentity:
        with self.b.lock:
            account = self.b.accounts.get(identity.account_id)
            if not self._mine(identity) or account is None or account.owner_user_id != identity.owner_user_id:
                raise PermissionError("row outside the caller's scope")
            for other in self.b.identities.values():
                if other.id != identity.id and other.account_id == identity.account_id:
                    if other.address == identity.address:
                        raise ValueError("this mailbox already sends as that address")
                    if other.is_default and identity.is_default:
                        raise ValueError("only one default identity")
            self.b.identities[identity.id] = identity.model_copy()
        return identity

    def delete_identity(self, identity_id: str) -> bool:
        with self.b.lock:
            found = self.b.identities.get(identity_id)
            if not found or not self._mine(found):
                return False
            del self.b.identities[identity_id]
            for draft in self.b.drafts.values():  # ON DELETE SET NULL
                if draft.identity_id == identity_id:
                    draft.identity_id = None
            return True

    # -- contacts ---------------------------------------------------------------------------

    def list_contacts(self, query: str | None, after: tuple[str, str] | None, limit: int) -> tuple[list[MailContact], int]:
        with self.b.lock:
            items = [c.model_copy() for c in self.b.contacts.values() if self._mine(c) and (not query or contact_matches(c, query))]
        items.sort(key=contact_sort_key)
        total = len(items)
        if after is not None:
            items = [c for c in items if contact_sort_key(c) > after]
        return items[:limit], total

    def get_contact(self, contact_id: str) -> MailContact | None:
        with self.b.lock:
            found = self.b.contacts.get(contact_id)
            return found.model_copy() if found and self._mine(found) else None

    def find_contact(self, email: str) -> MailContact | None:
        with self.b.lock:
            found = next((c for c in self.b.contacts.values() if self._mine(c) and c.email == email.lower()), None)
            return found.model_copy() if found else None

    def save_contact(self, contact: MailContact) -> MailContact:
        if not self._mine(contact):
            raise PermissionError("row outside the caller's scope")
        with self.b.lock:
            if any(c.id != contact.id and self._mine(c) and c.email == contact.email for c in self.b.contacts.values()):
                raise ValueError("a contact with this address already exists")
            self.b.contacts[contact.id] = contact.model_copy()
        return contact

    def delete_contact(self, contact_id: str) -> bool:
        with self.b.lock:
            found = self.b.contacts.get(contact_id)
            if not found or not self._mine(found):
                return False
            del self.b.contacts[contact_id]
            return True

    def count_contacts(self) -> int:
        with self.b.lock:
            return sum(1 for c in self.b.contacts.values() if self._mine(c))

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
