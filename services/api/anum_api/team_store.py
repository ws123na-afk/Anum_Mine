"""Storage for team work: one interface, an in-memory and a PostgreSQL implementation.

``ANUM_REPOSITORY_BACKEND=memory`` (local and tests) keeps everything in this process;
``postgresql`` uses the RLS-forced tables of migration ``0039_team_work`` through the
request's own session (``anum_api/db/team_repository.py``), so a work item, its audit
record, its activity entry, its notifications and its event commit together.

Every method takes the caller's :class:`TenantContext` and touches only that tenant and
workspace. Notification reads and changes are also limited to ``context.user_id`` (the
recipient); inserting a notification for a colleague is allowed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Protocol

from .schemas import TenantContext
from .team_models import (
    OPEN_STATUSES,
    PRIORITY_RANK,
    ActivityEntry,
    AssigneeType,
    AssistantProfile,
    ChecklistItem,
    EmailStatus,
    MemberProfile,
    UserNotification,
    WorkAttachment,
    WorkComment,
    WorkItem,
    WorkPriority,
    WorkStatus,
)


@dataclass
class WorkFilter:
    assignee_type: AssigneeType | None = None
    assignee_id: str | None = None
    created_by: str | None = None
    statuses: frozenset[WorkStatus] | None = None
    priority: WorkPriority | None = None
    overdue_at: datetime | None = None  # only open items due before this instant
    query: str | None = None
    sort: str = "due"  # due | updated | priority


@dataclass
class ItemCounts:
    checklist_total: int = 0
    checklist_done: int = 0
    comments: int = 0
    attachments: int = 0


class TeamStore(Protocol):
    # people
    def list_profiles(self, context: TenantContext) -> dict[str, MemberProfile]: ...
    def save_profile(self, context: TenantContext, profile: MemberProfile) -> MemberProfile: ...
    # assistants
    def list_assistants(self, context: TenantContext) -> list[AssistantProfile]: ...
    def get_assistant(self, context: TenantContext, assistant_id: str) -> AssistantProfile | None: ...
    def save_assistant(self, context: TenantContext, assistant: AssistantProfile) -> AssistantProfile: ...
    def delete_assistant(self, context: TenantContext, assistant_id: str) -> bool: ...
    # work items
    def get_item(self, context: TenantContext, item_id: str, *, for_update: bool = False) -> WorkItem | None: ...
    def insert_item(self, context: TenantContext, item: WorkItem) -> WorkItem: ...
    def update_item(self, context: TenantContext, item: WorkItem, expected_version: int) -> bool: ...
    def delete_item(self, context: TenantContext, item_id: str) -> bool: ...
    def query_items(self, context: TenantContext, flt: WorkFilter, limit: int, offset: int) -> tuple[list[WorkItem], int]: ...
    def open_items(self, context: TenantContext, limit: int) -> list[WorkItem]: ...
    def item_counts(self, context: TenantContext, item_ids: Iterable[str]) -> dict[str, ItemCounts]: ...
    # checklist, comments, attachments
    def list_checklist(self, context: TenantContext, item_id: str) -> list[ChecklistItem]: ...
    def save_checklist(self, context: TenantContext, item_id: str, entry: ChecklistItem) -> ChecklistItem: ...
    def delete_checklist(self, context: TenantContext, item_id: str, entry_id: str) -> bool: ...
    def list_comments(self, context: TenantContext, item_id: str) -> list[WorkComment]: ...
    def add_comment(self, context: TenantContext, item_id: str, comment: WorkComment) -> WorkComment: ...
    def delete_comment(self, context: TenantContext, item_id: str, comment_id: str) -> bool: ...
    def list_attachments(self, context: TenantContext, item_id: str) -> list[WorkAttachment]: ...
    def add_attachment(self, context: TenantContext, item_id: str, attachment: WorkAttachment) -> WorkAttachment: ...
    def delete_attachment(self, context: TenantContext, item_id: str, attachment_id: str) -> bool: ...
    # activity
    def add_activity(self, context: TenantContext, entry: ActivityEntry) -> ActivityEntry: ...
    def list_activity(
        self, context: TenantContext, *, limit: int, before: str | None, work_item_id: str | None
    ) -> list[ActivityEntry]: ...
    # notifications
    def add_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification: ...
    def list_notifications(
        self, context: TenantContext, *, limit: int, before: str | None, unread_only: bool
    ) -> list[UserNotification]: ...
    def count_unread(self, context: TenantContext) -> int: ...
    def get_notification(self, context: TenantContext, notification_id: str) -> UserNotification | None: ...
    def save_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification: ...
    def mark_all_read(self, context: TenantContext, at: datetime) -> int: ...


class UnknownCursorError(LookupError):
    """A ``before`` cursor that names no row in the caller's scope."""


def _matches(item: WorkItem, flt: WorkFilter) -> bool:
    if flt.assignee_type is not None and item.assignee_type != flt.assignee_type:
        return False
    if flt.assignee_id is not None and item.assignee_id != flt.assignee_id:
        return False
    if flt.created_by is not None and item.created_by != flt.created_by:
        return False
    if flt.statuses is not None and item.status not in flt.statuses:
        return False
    if flt.priority is not None and item.priority != flt.priority:
        return False
    if flt.overdue_at is not None and not (
        item.status in OPEN_STATUSES and item.due_at is not None and item.due_at < flt.overdue_at
    ):
        return False
    if flt.query:
        needle = flt.query.casefold()
        if needle not in item.title.casefold() and needle not in item.description.casefold():
            return False
    return True


def sort_key(sort: str):  # type: ignore[no-untyped-def]
    far = datetime.max.replace(tzinfo=None)

    def due(item: WorkItem):  # type: ignore[no-untyped-def]
        moment = item.due_at.replace(tzinfo=None) if item.due_at else far
        return (item.due_at is None, moment, PRIORITY_RANK[item.priority], item.id)

    if sort == "updated":
        return lambda item: (-item.updated_at.timestamp(), item.id)
    if sort == "priority":
        return lambda item: (PRIORITY_RANK[item.priority], *due(item)[:2], item.id)
    return due


@dataclass
class _Workspace:
    profiles: dict[str, MemberProfile] = field(default_factory=dict)
    assistants: dict[str, AssistantProfile] = field(default_factory=dict)
    items: dict[str, WorkItem] = field(default_factory=dict)
    checklist: dict[str, dict[str, ChecklistItem]] = field(default_factory=dict)
    comments: dict[str, dict[str, WorkComment]] = field(default_factory=dict)
    attachments: dict[str, dict[str, WorkAttachment]] = field(default_factory=dict)
    activity: list[ActivityEntry] = field(default_factory=list)
    notifications: dict[str, UserNotification] = field(default_factory=dict)


class InMemoryTeamStore:
    """``ANUM_REPOSITORY_BACKEND=memory``: lost on restart."""

    def __init__(self) -> None:
        self._scopes: dict[tuple[str, str], _Workspace] = {}
        self._lock = RLock()

    def _ws(self, context: TenantContext) -> _Workspace:
        with self._lock:
            return self._scopes.setdefault((context.tenant_id, context.workspace_id), _Workspace())

    def clear(self) -> None:
        with self._lock:
            self._scopes.clear()

    def items_for_tenant(self, tenant_id: str) -> list[WorkItem]:
        """Every work item of one tenant (the in-memory platform counts)."""
        with self._lock:
            return [i for (t, _), ws in self._scopes.items() if t == tenant_id for i in ws.items.values()]

    def scopes(self) -> list[tuple[str, str]]:
        """Every tenant and workspace with team data (the in-memory reminder sweep)."""
        with self._lock:
            return list(self._scopes)

    # people
    def list_profiles(self, context: TenantContext) -> dict[str, MemberProfile]:
        return dict(self._ws(context).profiles)

    def save_profile(self, context: TenantContext, profile: MemberProfile) -> MemberProfile:
        self._ws(context).profiles[profile.user_id] = profile
        return profile

    # assistants
    def list_assistants(self, context: TenantContext) -> list[AssistantProfile]:
        return sorted(self._ws(context).assistants.values(), key=lambda a: (a.name.casefold(), a.id))

    def get_assistant(self, context: TenantContext, assistant_id: str) -> AssistantProfile | None:
        return self._ws(context).assistants.get(assistant_id)

    def save_assistant(self, context: TenantContext, assistant: AssistantProfile) -> AssistantProfile:
        self._ws(context).assistants[assistant.id] = assistant
        return assistant

    def delete_assistant(self, context: TenantContext, assistant_id: str) -> bool:
        return self._ws(context).assistants.pop(assistant_id, None) is not None

    # work items
    def get_item(self, context: TenantContext, item_id: str, *, for_update: bool = False) -> WorkItem | None:
        item = self._ws(context).items.get(item_id)
        return item.model_copy(deep=True) if item else None

    def insert_item(self, context: TenantContext, item: WorkItem) -> WorkItem:
        self._ws(context).items[item.id] = item.model_copy(deep=True)
        return item

    def update_item(self, context: TenantContext, item: WorkItem, expected_version: int) -> bool:
        ws = self._ws(context)
        with self._lock:
            current = ws.items.get(item.id)
            if current is None or current.version != expected_version:
                return False
            ws.items[item.id] = item.model_copy(deep=True)
            return True

    def delete_item(self, context: TenantContext, item_id: str) -> bool:
        ws = self._ws(context)
        for nested in (ws.checklist, ws.comments, ws.attachments):
            nested.pop(item_id, None)
        return ws.items.pop(item_id, None) is not None

    def query_items(self, context: TenantContext, flt: WorkFilter, limit: int, offset: int) -> tuple[list[WorkItem], int]:
        matched = sorted((i for i in self._ws(context).items.values() if _matches(i, flt)), key=sort_key(flt.sort))
        return [i.model_copy(deep=True) for i in matched[offset : offset + limit]], len(matched)

    def open_items(self, context: TenantContext, limit: int) -> list[WorkItem]:
        return [i for i in self._ws(context).items.values() if i.status in OPEN_STATUSES][:limit]

    def item_counts(self, context: TenantContext, item_ids: Iterable[str]) -> dict[str, ItemCounts]:
        ws = self._ws(context)
        out: dict[str, ItemCounts] = {}
        for item_id in item_ids:
            checks = ws.checklist.get(item_id, {}).values()
            out[item_id] = ItemCounts(
                checklist_total=len(checks),
                checklist_done=sum(1 for c in checks if c.done),
                comments=len(ws.comments.get(item_id, {})),
                attachments=len(ws.attachments.get(item_id, {})),
            )
        return out

    # checklist, comments, attachments
    def list_checklist(self, context: TenantContext, item_id: str) -> list[ChecklistItem]:
        return sorted(self._ws(context).checklist.get(item_id, {}).values(), key=lambda c: (c.position, c.created_at, c.id))

    def save_checklist(self, context: TenantContext, item_id: str, entry: ChecklistItem) -> ChecklistItem:
        self._ws(context).checklist.setdefault(item_id, {})[entry.id] = entry
        return entry

    def delete_checklist(self, context: TenantContext, item_id: str, entry_id: str) -> bool:
        return self._ws(context).checklist.get(item_id, {}).pop(entry_id, None) is not None

    def list_comments(self, context: TenantContext, item_id: str) -> list[WorkComment]:
        return sorted(self._ws(context).comments.get(item_id, {}).values(), key=lambda c: (c.created_at, c.id))

    def add_comment(self, context: TenantContext, item_id: str, comment: WorkComment) -> WorkComment:
        self._ws(context).comments.setdefault(item_id, {})[comment.id] = comment
        return comment

    def delete_comment(self, context: TenantContext, item_id: str, comment_id: str) -> bool:
        return self._ws(context).comments.get(item_id, {}).pop(comment_id, None) is not None

    def list_attachments(self, context: TenantContext, item_id: str) -> list[WorkAttachment]:
        return sorted(self._ws(context).attachments.get(item_id, {}).values(), key=lambda a: (a.created_at, a.id))

    def add_attachment(self, context: TenantContext, item_id: str, attachment: WorkAttachment) -> WorkAttachment:
        existing = self._ws(context).attachments.setdefault(item_id, {})
        if any(a.file_id == attachment.file_id for a in existing.values()):
            raise ValueError("This file is already attached")
        existing[attachment.id] = attachment
        return attachment

    def delete_attachment(self, context: TenantContext, item_id: str, attachment_id: str) -> bool:
        return self._ws(context).attachments.get(item_id, {}).pop(attachment_id, None) is not None

    # activity (newest first)
    def add_activity(self, context: TenantContext, entry: ActivityEntry) -> ActivityEntry:
        self._ws(context).activity.append(entry)
        return entry

    def list_activity(
        self, context: TenantContext, *, limit: int, before: str | None, work_item_id: str | None
    ) -> list[ActivityEntry]:
        rows = sorted(self._ws(context).activity, key=lambda a: (a.created_at, a.id), reverse=True)
        if work_item_id is not None:
            rows = [a for a in rows if a.work_item_id == work_item_id]
        if before is not None:
            index = next((n for n, a in enumerate(rows) if a.id == before), None)
            if index is None:
                raise UnknownCursorError(before)
            rows = rows[index + 1 :]
        return rows[:limit]

    # notifications (newest first, recipient only)
    def _mine(self, context: TenantContext) -> list[UserNotification]:
        return sorted(
            (n for n in self._ws(context).notifications.values() if n.user_id == context.user_id),
            key=lambda n: (n.created_at, n.id),
            reverse=True,
        )

    def add_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification:
        self._ws(context).notifications[notification.id] = notification
        return notification

    def list_notifications(
        self, context: TenantContext, *, limit: int, before: str | None, unread_only: bool
    ) -> list[UserNotification]:
        rows = self._mine(context)
        if before is not None:
            index = next((n for n, x in enumerate(rows) if x.id == before), None)
            if index is None:
                raise UnknownCursorError(before)
            rows = rows[index + 1 :]
        if unread_only:
            rows = [n for n in rows if n.read_at is None]
        return rows[:limit]

    def count_unread(self, context: TenantContext) -> int:
        return sum(1 for n in self._mine(context) if n.read_at is None)

    def get_notification(self, context: TenantContext, notification_id: str) -> UserNotification | None:
        found = self._ws(context).notifications.get(notification_id)
        return found if found is not None and found.user_id == context.user_id else None

    def save_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification:
        if notification.user_id != context.user_id:
            raise PermissionError("notifications can only be changed by their recipient")
        self._ws(context).notifications[notification.id] = notification
        return notification

    def mark_all_read(self, context: TenantContext, at: datetime) -> int:
        changed = 0
        for notification in self._mine(context):
            if notification.read_at is None:
                notification.read_at = at
                changed += 1
        return changed

    def pending_email(self) -> list[tuple[str, str, str, str]]:
        """(tenant, workspace, user, id) of notifications whose email is still pending."""
        with self._lock:
            return [
                (tenant, workspace, n.user_id, n.id)
                for (tenant, workspace), ws in self._scopes.items()
                for n in ws.notifications.values()
                if n.email_status == EmailStatus.PENDING and n.email_attempts < 3
            ]


memory_team_store = InMemoryTeamStore()
