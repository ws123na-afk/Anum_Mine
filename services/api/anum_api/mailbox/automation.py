"""Mailbox settings, forwarding rules and background sync state (migration
``0068_mail_rules_retention``, docs/email.md#mailbox-settings).

What is stored, per mailbox:

* **Settings**: how many days ANUM keeps messages written in it once sent, unsent drafts and AI
  labels (``None`` keeps them while the mailbox is connected), and whether the server checks the
  mailbox in the background. Only an approved ``change_mail_settings`` call changes them, and only
  while the settings are still at the version the change was asked against.
* **Forwarding rules**: forward new inbox mail that matches to the addresses named. A rule
  forwards only while it is ``active`` with ``approved_hash`` equal to the hash of what it does
  now (set by an approved ``activate_mail_forward_rule`` call; any edit clears it), and every run
  checks that again.
* **Sync state**: the last unread and total counts of the inbox the background sync saw, so the
  sidebar badge is current while the Email page is closed.

Settings and sync state are visible to the mailbox's owner and the people it is shared with;
rules and their log only to the owner. With PostgreSQL that is forced RLS plus restrictive
policies, and every query repeats the scope; the memory store behaves the same.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext, utc_now
from ..scoped_store import open_scoped_store

RuleStatus = Literal["pending_approval", "active", "paused"]
SyncStatus = Literal["pending", "ok", "auth_failed", "offline", "error"]
LogStatus = Literal["forwarding", "forwarded", "failed", "unknown"]
RETENTION_CHOICES = (7, 30, 90, 180, 365, 730)
MAX_RETENTION_DAYS = 3650
MAX_RULES = 20
MAX_FORWARD_TO = 5
SYNC_LEASE = timedelta(minutes=5)


class MailboxSettings(BaseModel):
    account_id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    sent_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    draft_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    label_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    background_sync: bool = True
    version: int = 0
    updated_by: str | None = None
    pending_approval_id: str | None = None
    pending_task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    # ``_retire`` (email_api) withdraws a waiting change through these two names.
    @property
    def approval_id(self) -> str | None:
        return self.pending_approval_id

    @property
    def task_id(self) -> str | None:
        return self.pending_task_id


SETTING_FIELDS = ("sent_retention_days", "draft_retention_days", "label_retention_days", "background_sync")


def settings_values(item: MailboxSettings) -> dict[str, Any]:
    return {key: getattr(item, key) for key in SETTING_FIELDS}


class SyncState(BaseModel):
    account_id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    inbox_unread: int | None = None
    inbox_total: int | None = None
    status: SyncStatus = "pending"
    last_error: str | None = None
    checked_at: datetime | None = None
    next_sync_at: datetime = Field(default_factory=utc_now)
    lease_until: datetime | None = None


class ForwardRule(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    name: str
    from_contains: str = ""
    subject_contains: str = ""
    forward_to: list[str]
    status: RuleStatus = "pending_approval"
    rule_hash: str = ""
    approved_hash: str | None = None
    approval_id: str | None = None
    task_id: str | None = None
    approved_at: datetime | None = None
    last_uid: int | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    forwarded_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ForwardLog(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    rule_id: str
    uid: int
    message_id: str | None = None
    status: LogStatus = "forwarding"
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


def rule_hash(account_id: str, owner_user_id: str, name: str, from_contains: str, subject_contains: str, forward_to: list[str]) -> str:
    """What a rule does, as one hash: the mailbox, its owner, the conditions and the addresses."""
    body = {
        "account_id": account_id, "owner_user_id": owner_user_id, "name": name, "from_contains": from_contains,
        "subject_contains": subject_contains, "forward_to": sorted(forward_to),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hash_of(rule: ForwardRule) -> str:
    return rule_hash(rule.account_id, rule.owner_user_id, rule.name, rule.from_contains, rule.subject_contains, rule.forward_to)


def rule_matches(rule: ForwardRule, sender: str, subject: str) -> bool:
    """Every condition the rule has must hold (case-insensitive "contains")."""
    if rule.from_contains and rule.from_contains.lower() not in sender.lower():
        return False
    if rule.subject_contains and rule.subject_contains.lower() not in subject.lower():
        return False
    return bool(rule.from_contains or rule.subject_contains)


class AutomationStore(Protocol):
    def get_settings(self, account_id: str) -> MailboxSettings | None: ...
    def save_settings(self, item: MailboxSettings, expected_version: int | None) -> bool: ...
    def set_pending(self, account_id: str, approval_id: str | None, task_id: str | None) -> None: ...
    def get_sync(self, account_id: str) -> SyncState | None: ...
    def sync_for(self, account_ids: list[str]) -> dict[str, SyncState]: ...
    def claim_sync(self, state: SyncState, now: datetime, lease_until: datetime) -> bool: ...
    def save_sync(self, state: SyncState) -> None: ...
    def list_rules(self, account_id: str) -> list[ForwardRule]: ...
    def all_rules(self) -> list[ForwardRule]: ...
    def get_rule(self, rule_id: str) -> ForwardRule | None: ...
    def save_rule(self, rule: ForwardRule) -> ForwardRule: ...
    def delete_rule(self, rule_id: str) -> bool: ...
    def claim_forward(self, log: ForwardLog) -> bool: ...
    def finish_forward(self, rule_id: str, uid: int, status: LogStatus, error: str | None) -> None: ...
    def count_forwards_since(self, rule_ids: list[str], since: datetime) -> int: ...
    def record_run(self, rule_id: str, approved_hash: str, last_uid: int | None, forwarded: int, error: str | None, now: datetime) -> bool: ...
    def refuse_rule(self, rule_id: str, error: str, now: datetime) -> None: ...


class InMemoryAutomationBackend:
    def __init__(self) -> None:
        self.settings: dict[str, MailboxSettings] = {}
        self.sync: dict[str, SyncState] = {}
        self.rules: dict[str, ForwardRule] = {}
        self.log: dict[tuple[str, int], ForwardLog] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.settings.clear()
            self.sync.clear()
            self.rules.clear()
            self.log.clear()


class MemoryAutomationScope:
    """Behaves like the row policies, and like the foreign keys: rows of a removed mailbox are gone."""

    def __init__(self, backend: InMemoryAutomationBackend, context: TenantContext) -> None:
        self.b = backend
        self.scope = (context.tenant_id, context.workspace_id, context.user_id)

    def _account_exists(self, account_id: str) -> bool:
        from .store import mail_backend

        return account_id in mail_backend.accounts

    def _mine(self, item: MailboxSettings | SyncState | ForwardRule | ForwardLog) -> bool:
        return (item.tenant_id, item.workspace_id, item.owner_user_id) == self.scope

    def _visible(self, item: MailboxSettings | SyncState) -> bool:
        if not self._account_exists(item.account_id) or (item.tenant_id, item.workspace_id) != self.scope[:2]:
            return False
        if item.owner_user_id == self.scope[2]:
            return True
        from .store import mail_backend

        return any(g.account_id == item.account_id and g.grantee_user_id == self.scope[2] for g in mail_backend.grants.values())

    def _check(self, item: MailboxSettings | SyncState | ForwardRule | ForwardLog) -> None:
        if not self._mine(item):
            raise PermissionError("row outside the caller's scope")

    def get_settings(self, account_id: str) -> MailboxSettings | None:
        with self.b.lock:
            found = self.b.settings.get(account_id)
            return found.model_copy() if found and self._visible(found) else None

    def save_settings(self, item: MailboxSettings, expected_version: int | None) -> bool:
        self._check(item)
        with self.b.lock:
            current = self.b.settings.get(item.account_id)
            if current is not None and not self._account_exists(current.account_id):
                current = None
            if (current.version if current else None) != expected_version:
                return False
            self.b.settings[item.account_id] = item.model_copy()
        return True

    def set_pending(self, account_id: str, approval_id: str | None, task_id: str | None) -> None:
        with self.b.lock:
            found = self.b.settings.get(account_id)
            if found is not None and self._mine(found):
                self.b.settings[account_id] = found.model_copy(update={"pending_approval_id": approval_id, "pending_task_id": task_id})

    def get_sync(self, account_id: str) -> SyncState | None:
        with self.b.lock:
            found = self.b.sync.get(account_id)
            return found.model_copy() if found and self._visible(found) else None

    def sync_for(self, account_ids: list[str]) -> dict[str, SyncState]:
        return {key: value for key in account_ids if (value := self.get_sync(key)) is not None}

    def claim_sync(self, state: SyncState, now: datetime, lease_until: datetime) -> bool:
        self._check(state)
        with self.b.lock:
            current = self.b.sync.get(state.account_id)
            # Another replica holds it, or it was checked since it was found due.
            if current is not None and ((current.lease_until is not None and current.lease_until >= now) or current.next_sync_at > now):
                return False
            claimed = (current or state).model_copy(update={"lease_until": lease_until})
            self.b.sync[state.account_id] = claimed
        return True

    def save_sync(self, state: SyncState) -> None:
        self._check(state)
        with self.b.lock:
            self.b.sync[state.account_id] = state.model_copy()

    def list_rules(self, account_id: str) -> list[ForwardRule]:
        with self.b.lock:
            rows = [r.model_copy() for r in self.b.rules.values() if self._mine(r) and r.account_id == account_id and self._account_exists(account_id)]
        return sorted(rows, key=lambda r: (r.created_at, r.id))

    def all_rules(self) -> list[ForwardRule]:
        with self.b.lock:
            rows = [r.model_copy() for r in self.b.rules.values() if self._mine(r) and self._account_exists(r.account_id)]
        return sorted(rows, key=lambda r: (r.created_at, r.id))

    def get_rule(self, rule_id: str) -> ForwardRule | None:
        with self.b.lock:
            found = self.b.rules.get(rule_id)
            return found.model_copy() if found and self._mine(found) and self._account_exists(found.account_id) else None

    def save_rule(self, rule: ForwardRule) -> ForwardRule:
        self._check(rule)
        if rule.status == "active" and rule.approved_hash != rule.rule_hash:
            raise ValueError("an active rule must carry its approved hash")
        with self.b.lock:
            self.b.rules[rule.id] = rule.model_copy()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        with self.b.lock:
            found = self.b.rules.get(rule_id)
            if found is None or not self._mine(found):
                return False
            del self.b.rules[rule_id]
            for key in [k for k in self.b.log if k[0] == rule_id]:
                del self.b.log[key]
        return True

    def claim_forward(self, log: ForwardLog) -> bool:
        self._check(log)
        with self.b.lock:
            if (log.rule_id, log.uid) in self.b.log:
                return False
            self.b.log[(log.rule_id, log.uid)] = log.model_copy()
        return True

    def finish_forward(self, rule_id: str, uid: int, status: LogStatus, error: str | None) -> None:
        with self.b.lock:
            found = self.b.log.get((rule_id, uid))
            if found is not None and self._mine(found):
                self.b.log[(rule_id, uid)] = found.model_copy(update={"status": status, "error": error})

    def count_forwards_since(self, rule_ids: list[str], since: datetime) -> int:
        with self.b.lock:
            return sum(1 for (rule, _), row in self.b.log.items() if rule in rule_ids and self._mine(row) and row.created_at >= since)

    def record_run(self, rule_id: str, approved_hash: str, last_uid: int | None, forwarded: int, error: str | None, now: datetime) -> bool:
        """A pass's progress, only while the rule is still active on the hash the pass checked."""
        with self.b.lock:
            found = self.b.rules.get(rule_id)
            if found is None or not self._mine(found) or found.status != "active" or found.approved_hash != approved_hash:
                return False
            self.b.rules[rule_id] = found.model_copy(update={
                "last_uid": last_uid, "last_run_at": now, "last_error": error, "forwarded_count": found.forwarded_count + forwarded,
            })
        return True

    def refuse_rule(self, rule_id: str, error: str, now: datetime) -> None:
        """A rule that no longer passes its checks stops (paused, approval cleared) and says why."""
        with self.b.lock:
            found = self.b.rules.get(rule_id)
            if found is not None and self._mine(found):
                self.b.rules[rule_id] = found.model_copy(update={"status": "paused", "approved_hash": None, "last_error": error, "updated_at": now})


automation_backend = InMemoryAutomationBackend()


@contextmanager
def open_automation_store(context: TenantContext) -> Iterator[AutomationStore]:
    """One unit of work as the caller (tenant, workspace and user RLS with PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.mail_automation_repository import SqlAlchemyAutomationStore

        return SqlAlchemyAutomationStore(session, context)

    with open_scoped_store(context, MemoryAutomationScope(automation_backend, context), sql_store, user_scoped=True) as store:
        yield store


def default_settings(account_id: str, tenant_id: str, workspace_id: str, owner_user_id: str) -> MailboxSettings:
    return MailboxSettings(account_id=account_id, tenant_id=tenant_id, workspace_id=workspace_id, owner_user_id=owner_user_id)
