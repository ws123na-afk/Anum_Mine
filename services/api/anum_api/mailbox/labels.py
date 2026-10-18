"""Stored labels from AI on email (migration ``0063_mail_labels``, docs/email.md#ai-on-email).

A label is the category and priority AI (or ANUM's rules) gave one message, kept so the list
can show it. It belongs to the person who asked for it: forced RLS plus a restrictive owner
policy with PostgreSQL, and every query here repeats the owner. A label carries the message's
Message-ID; the list shows it only while the message at that UID still has that Message-ID,
so a reused UID never shows another message's label. No subject, sender or body is stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext, utc_now
from ..scoped_store import open_scoped_store


class MailLabel(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    folder: str
    uid: int
    message_id: str | None = None
    category: str
    priority: Literal["high", "normal", "low"]
    reason: str = ""
    source: Literal["model", "rules"]
    model: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MailLabelStore(Protocol):
    def labels_for(self, account_id: str, folder: str, uids: list[int]) -> dict[int, MailLabel]: ...
    def save_label(self, label: MailLabel) -> MailLabel: ...
    def list_all(self) -> list[MailLabel]: ...
    def delete_all(self) -> int: ...


class InMemoryLabelBackend:
    def __init__(self) -> None:
        self.labels: dict[tuple[str, str, str, str, str, int], MailLabel] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.labels.clear()


class MemoryLabelScope:
    """Behaves like the row policies: own tenant, workspace and person only."""

    def __init__(self, backend: InMemoryLabelBackend, context: TenantContext) -> None:
        self.b = backend
        self.scope = (context.tenant_id, context.workspace_id, context.user_id)

    def _key(self, account_id: str, folder: str, uid: int) -> tuple[str, str, str, str, str, int]:
        return (*self.scope, account_id, folder, uid)

    def labels_for(self, account_id: str, folder: str, uids: list[int]) -> dict[int, MailLabel]:
        with self.b.lock:
            found = {uid: self.b.labels.get(self._key(account_id, folder, uid)) for uid in uids}
        return {uid: label.model_copy() for uid, label in found.items() if label is not None}

    def save_label(self, label: MailLabel) -> MailLabel:
        if (label.tenant_id, label.workspace_id, label.owner_user_id) != self.scope:
            raise PermissionError("row outside the caller's scope")
        key = self._key(label.account_id, label.folder, label.uid)
        with self.b.lock:
            existing = self.b.labels.get(key)
            if existing is not None:
                label = label.model_copy(update={"id": existing.id, "created_at": existing.created_at})
            self.b.labels[key] = label.model_copy()
        return label

    def list_all(self) -> list[MailLabel]:
        with self.b.lock:
            return [label.model_copy() for key, label in self.b.labels.items() if key[:3] == self.scope]

    def delete_all(self) -> int:
        with self.b.lock:
            keys = [key for key in self.b.labels if key[:3] == self.scope]
            for key in keys:
                del self.b.labels[key]
        return len(keys)

    def forget_account(self, account_id: str) -> None:
        """Memory mode has no foreign key: a disconnected mailbox takes its labels along."""
        with self.b.lock:
            for key in [key for key in self.b.labels if key[3] == account_id]:
                del self.b.labels[key]


label_backend = InMemoryLabelBackend()


@contextmanager
def open_label_store(context: TenantContext) -> Iterator[MailLabelStore]:
    """One unit of work as the caller (tenant, workspace and user RLS with PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.mail_labels_repository import SqlAlchemyLabelStore

        return SqlAlchemyLabelStore(session, context)

    with open_scoped_store(context, MemoryLabelScope(label_backend, context), sql_store, user_scoped=True) as store:
        yield store


def visible_labels(labels: dict[int, MailLabel], message_ids: dict[int, str | None]) -> dict[int, MailLabel]:
    """Only labels whose message still sits at that UID (same Message-ID, when both have one)."""
    shown: dict[int, MailLabel] = {}
    for uid, label in labels.items():
        current = message_ids.get(uid)
        if label.message_id and current and label.message_id != current:
            continue
        shown[uid] = label
    return shown
