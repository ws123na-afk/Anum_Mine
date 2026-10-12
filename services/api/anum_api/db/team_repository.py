"""PostgreSQL storage for team work (migration ``0039_team_work``).

Every statement names the caller's tenant and workspace explicitly *and* runs inside the
session's RLS context, so a missing filter can never reach another workspace. Reads and
changes of ``user_notifications`` need ``anum.user_id`` in the session (the recipient);
inserts do not, so an assignment can notify a colleague. Inserts set every column and do
not use ``RETURNING``, because a notification for someone else is not readable back by
the person who created it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    and_,
    case,
    delete,
    func,
    insert,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..schemas import TenantContext
from ..team_models import (
    OPEN_STATUSES,
    ActivityEntry,
    AssistantProfile,
    ChecklistItem,
    MemberProfile,
    UserNotification,
    WorkAttachment,
    WorkComment,
    WorkItem,
)
from ..team_store import ItemCounts, UnknownCursorError, WorkFilter
from .models import Base

_md = Base.metadata


def _scope() -> list[Column]:
    return [Column("tenant_id", String(80), primary_key=True), Column("workspace_id", String(80), primary_key=True)]


team_member_profiles = Table(
    "team_member_profiles", _md, *_scope(),
    Column("user_id", String(160), primary_key=True),
    Column("display_name", String(120), nullable=False),
    Column("email", String(254)),
    Column("department", String(40)),
    Column("job_title", String(120)),
    Column("capacity_hours", Integer),
    Column("timezone", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
assistant_profiles = Table(
    "assistant_profiles", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("department", String(40), nullable=False),
    Column("name", String(120), nullable=False),
    Column("instructions", Text, nullable=False),
    Column("allowed_tools", JSONB, nullable=False),
    Column("routing_keywords", JSONB, nullable=False),
    Column("inbound_channels", JSONB, nullable=False),
    Column("autonomy", String(20), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_by_user_id", String(160), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
work_items = Table(
    "work_items", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("priority", String(10), nullable=False),
    Column("assignee_type", String(12)),
    Column("assignee_id", String(160)),
    Column("created_by_user_id", String(160), nullable=False),
    Column("due_at", DateTime(timezone=True)),
    Column("follow_up_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("reminder_sent_at", DateTime(timezone=True)),
    Column("estimate_hours", Float),
    Column("source", String(20), nullable=False),
    Column("source_detail", JSONB, nullable=False),
    Column("agent_task_id", String(80)),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
work_item_comments = Table(
    "work_item_comments", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("work_item_id", String(80), nullable=False),
    Column("author_type", String(12), nullable=False),
    Column("author_id", String(160), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
work_item_checklist = Table(
    "work_item_checklist", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("work_item_id", String(80), nullable=False),
    Column("label", String(300), nullable=False),
    Column("done", Boolean, nullable=False),
    Column("position", Integer, nullable=False),
    Column("done_by_user_id", String(160)),
    Column("done_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
work_item_attachments = Table(
    "work_item_attachments", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("work_item_id", String(80), nullable=False),
    Column("file_id", String(80), nullable=False),
    Column("file_name", String(255), nullable=False),
    Column("added_by_user_id", String(160), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
team_activity = Table(
    "team_activity", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("work_item_id", String(80)),
    Column("actor_type", String(12), nullable=False),
    Column("actor_id", String(160), nullable=False),
    Column("action", String(60), nullable=False),
    Column("summary", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
user_notifications = Table(
    "user_notifications", _md, *_scope(),
    Column("id", String(80), primary_key=True),
    Column("user_id", String(160), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", String(1000), nullable=False),
    Column("work_item_id", String(80)),
    Column("read_at", DateTime(timezone=True)),
    Column("email_status", String(20), nullable=False),
    Column("email_attempts", Integer, nullable=False),
    Column("email_error", String(300)),
    Column("push_status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
tenant_branding = Table(
    "tenant_branding", _md,
    Column("tenant_id", String(80), primary_key=True),
    Column("display_name", String(160), nullable=False),
    Column("logo", LargeBinary),
    Column("logo_content_type", String(40)),
    Column("updated_by_user_id", String(160), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
platform_organizations = Table(
    "platform_organizations", _md,
    Column("tenant_id", String(80), primary_key=True),
    Column("operator_user_id", String(160), nullable=False),
    Column("display_name", String(160), nullable=False),
    Column("plan", String(40), nullable=False),
    Column("first_workspace_id", String(80), nullable=False),
    Column("first_admin_email", String(254), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _in_scope(table: Table, context: TenantContext) -> Any:
    return and_(table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


def _scope_values(context: TenantContext) -> dict[str, str]:
    return {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}


def _item_values(item: WorkItem) -> dict[str, Any]:
    data = item.model_dump(mode="python", exclude={"tenant_id", "workspace_id", "created_by"})
    data["created_by_user_id"] = item.created_by
    for key in ("status", "priority", "assignee_type", "source"):
        value = data[key]
        data[key] = value.value if hasattr(value, "value") else value
    return data


def _item(row: Any) -> WorkItem:
    data = dict(row._mapping)
    data["created_by"] = data.pop("created_by_user_id")
    return WorkItem(**data)


_PRIORITY_ORDER = case(
    {"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=work_items.c.priority, else_=4
)


class SqlAlchemyTeamStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # people ---------------------------------------------------------------------------
    def list_profiles(self, context: TenantContext) -> dict[str, MemberProfile]:
        rows = self.session.execute(select(team_member_profiles).where(_in_scope(team_member_profiles, context))).all()
        out: dict[str, MemberProfile] = {}
        for row in rows:
            data = dict(row._mapping)
            data.pop("tenant_id")
            data.pop("workspace_id")
            out[data["user_id"]] = MemberProfile(**data)
        return out

    def save_profile(self, context: TenantContext, profile: MemberProfile) -> MemberProfile:
        values = profile.model_dump(mode="python")
        values["department"] = profile.department.value if profile.department else None
        exists = self.session.execute(
            select(team_member_profiles.c.user_id).where(
                _in_scope(team_member_profiles, context), team_member_profiles.c.user_id == profile.user_id
            )
        ).first()
        if exists:
            self.session.execute(
                update(team_member_profiles)
                .where(_in_scope(team_member_profiles, context), team_member_profiles.c.user_id == profile.user_id)
                .values(**{k: v for k, v in values.items() if k != "user_id"})
            )
        else:
            self.session.execute(insert(team_member_profiles).values(**_scope_values(context), **values))
        return profile

    # assistants -----------------------------------------------------------------------
    @staticmethod
    def _assistant(row: Any) -> AssistantProfile:
        data = dict(row._mapping)
        data.pop("tenant_id")
        data.pop("workspace_id")
        data["created_by"] = data.pop("created_by_user_id")
        return AssistantProfile(**data)

    def list_assistants(self, context: TenantContext) -> list[AssistantProfile]:
        rows = self.session.execute(
            select(assistant_profiles)
            .where(_in_scope(assistant_profiles, context))
            .order_by(func.lower(assistant_profiles.c.name), assistant_profiles.c.id)
        ).all()
        return [self._assistant(row) for row in rows]

    def get_assistant(self, context: TenantContext, assistant_id: str) -> AssistantProfile | None:
        row = self.session.execute(
            select(assistant_profiles).where(_in_scope(assistant_profiles, context), assistant_profiles.c.id == assistant_id)
        ).first()
        return self._assistant(row) if row else None

    def save_assistant(self, context: TenantContext, assistant: AssistantProfile) -> AssistantProfile:
        values = assistant.model_dump(mode="json", exclude={"created_by", "created_at", "updated_at"})
        values.update(created_at=assistant.created_at, updated_at=assistant.updated_at, created_by_user_id=assistant.created_by)
        if self.get_assistant(context, assistant.id) is None:
            self.session.execute(insert(assistant_profiles).values(**_scope_values(context), **values))
        else:
            values.pop("id")
            values.pop("created_by_user_id")
            values.pop("created_at")
            self.session.execute(
                update(assistant_profiles)
                .where(_in_scope(assistant_profiles, context), assistant_profiles.c.id == assistant.id)
                .values(**values)
            )
        return assistant

    def delete_assistant(self, context: TenantContext, assistant_id: str) -> bool:
        result = self.session.execute(
            delete(assistant_profiles).where(_in_scope(assistant_profiles, context), assistant_profiles.c.id == assistant_id)
        )
        return bool(result.rowcount)

    # work items -----------------------------------------------------------------------
    def get_item(self, context: TenantContext, item_id: str, *, for_update: bool = False) -> WorkItem | None:
        statement = select(work_items).where(_in_scope(work_items, context), work_items.c.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        row = self.session.execute(statement).first()
        return _item(row) if row else None

    def insert_item(self, context: TenantContext, item: WorkItem) -> WorkItem:
        self.session.execute(insert(work_items).values(**_scope_values(context), **_item_values(item)))
        return item

    def update_item(self, context: TenantContext, item: WorkItem, expected_version: int) -> bool:
        values = _item_values(item)
        values.pop("id")
        result = self.session.execute(
            update(work_items)
            .where(_in_scope(work_items, context), work_items.c.id == item.id, work_items.c.version == expected_version)
            .values(**values)
        )
        return bool(result.rowcount)

    def delete_item(self, context: TenantContext, item_id: str) -> bool:
        result = self.session.execute(delete(work_items).where(_in_scope(work_items, context), work_items.c.id == item_id))
        return bool(result.rowcount)

    def _filtered(self, context: TenantContext, flt: WorkFilter) -> list[Any]:
        clauses: list[Any] = [_in_scope(work_items, context)]
        if flt.assignee_type is not None:
            clauses.append(work_items.c.assignee_type == flt.assignee_type.value)
        if flt.assignee_id is not None:
            clauses.append(work_items.c.assignee_id == flt.assignee_id)
        if flt.created_by is not None:
            clauses.append(work_items.c.created_by_user_id == flt.created_by)
        if flt.statuses is not None:
            clauses.append(work_items.c.status.in_([s.value for s in flt.statuses]))
        if flt.priority is not None:
            clauses.append(work_items.c.priority == flt.priority.value)
        if flt.overdue_at is not None:
            clauses.append(work_items.c.status.in_([s.value for s in OPEN_STATUSES]))
            clauses.append(work_items.c.due_at < flt.overdue_at)
        if flt.query:
            escaped = flt.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append(
                or_(work_items.c.title.ilike(pattern, escape="\\"), work_items.c.description.ilike(pattern, escape="\\"))
            )
        return clauses

    def query_items(self, context: TenantContext, flt: WorkFilter, limit: int, offset: int) -> tuple[list[WorkItem], int]:
        clauses = self._filtered(context, flt)
        total = self.session.execute(select(func.count()).select_from(work_items).where(*clauses)).scalar_one()
        if flt.sort == "updated":
            order = [work_items.c.updated_at.desc(), work_items.c.id.desc()]
        elif flt.sort == "priority":
            order = [_PRIORITY_ORDER, work_items.c.due_at.asc().nulls_last(), work_items.c.id]
        else:
            order = [work_items.c.due_at.asc().nulls_last(), _PRIORITY_ORDER, work_items.c.id]
        rows = self.session.execute(select(work_items).where(*clauses).order_by(*order).limit(limit).offset(offset)).all()
        return [_item(row) for row in rows], int(total)

    def open_items(self, context: TenantContext, limit: int) -> list[WorkItem]:
        rows = self.session.execute(
            select(work_items)
            .where(_in_scope(work_items, context), work_items.c.status.in_([s.value for s in OPEN_STATUSES]))
            .order_by(work_items.c.id)
            .limit(limit)
        ).all()
        return [_item(row) for row in rows]

    def item_counts(self, context: TenantContext, item_ids: Iterable[str]) -> dict[str, ItemCounts]:
        ids = list(item_ids)
        out = {item_id: ItemCounts() for item_id in ids}
        if not ids:
            return out
        checks = self.session.execute(
            select(
                work_item_checklist.c.work_item_id,
                func.count(),
                func.count().filter(work_item_checklist.c.done.is_(True)),
            )
            .where(_in_scope(work_item_checklist, context), work_item_checklist.c.work_item_id.in_(ids))
            .group_by(work_item_checklist.c.work_item_id)
        ).all()
        for item_id, total, done in checks:
            out[item_id].checklist_total, out[item_id].checklist_done = int(total), int(done)
        for table, attribute in ((work_item_comments, "comments"), (work_item_attachments, "attachments")):
            rows = self.session.execute(
                select(table.c.work_item_id, func.count())
                .where(_in_scope(table, context), table.c.work_item_id.in_(ids))
                .group_by(table.c.work_item_id)
            ).all()
            for item_id, count in rows:
                setattr(out[item_id], attribute, int(count))
        return out

    # checklist, comments, attachments ---------------------------------------------------
    def list_checklist(self, context: TenantContext, item_id: str) -> list[ChecklistItem]:
        rows = self.session.execute(
            select(work_item_checklist)
            .where(_in_scope(work_item_checklist, context), work_item_checklist.c.work_item_id == item_id)
            .order_by(work_item_checklist.c.position, work_item_checklist.c.created_at, work_item_checklist.c.id)
        ).all()
        return [
            ChecklistItem(
                id=r.id, label=r.label, done=r.done, position=r.position, done_by=r.done_by_user_id,
                done_at=r.done_at, created_at=r.created_at,
            )
            for r in rows
        ]

    def save_checklist(self, context: TenantContext, item_id: str, entry: ChecklistItem) -> ChecklistItem:
        values = {
            "label": entry.label, "done": entry.done, "position": entry.position,
            "done_by_user_id": entry.done_by, "done_at": entry.done_at,
        }
        result = self.session.execute(
            update(work_item_checklist)
            .where(_in_scope(work_item_checklist, context), work_item_checklist.c.id == entry.id,
                   work_item_checklist.c.work_item_id == item_id)
            .values(**values)
        )
        if not result.rowcount:
            self.session.execute(
                insert(work_item_checklist).values(
                    **_scope_values(context), id=entry.id, work_item_id=item_id, created_at=entry.created_at, **values
                )
            )
        return entry

    def delete_checklist(self, context: TenantContext, item_id: str, entry_id: str) -> bool:
        result = self.session.execute(
            delete(work_item_checklist).where(
                _in_scope(work_item_checklist, context), work_item_checklist.c.work_item_id == item_id,
                work_item_checklist.c.id == entry_id,
            )
        )
        return bool(result.rowcount)

    def list_comments(self, context: TenantContext, item_id: str) -> list[WorkComment]:
        rows = self.session.execute(
            select(work_item_comments)
            .where(_in_scope(work_item_comments, context), work_item_comments.c.work_item_id == item_id)
            .order_by(work_item_comments.c.created_at, work_item_comments.c.id)
        ).all()
        return [
            WorkComment(id=r.id, author_type=r.author_type, author_id=r.author_id, body=r.body, created_at=r.created_at)
            for r in rows
        ]

    def add_comment(self, context: TenantContext, item_id: str, comment: WorkComment) -> WorkComment:
        self.session.execute(
            insert(work_item_comments).values(
                **_scope_values(context), id=comment.id, work_item_id=item_id, author_type=comment.author_type.value,
                author_id=comment.author_id, body=comment.body, created_at=comment.created_at,
            )
        )
        return comment

    def delete_comment(self, context: TenantContext, item_id: str, comment_id: str) -> bool:
        result = self.session.execute(
            delete(work_item_comments).where(
                _in_scope(work_item_comments, context), work_item_comments.c.work_item_id == item_id,
                work_item_comments.c.id == comment_id,
            )
        )
        return bool(result.rowcount)

    def list_attachments(self, context: TenantContext, item_id: str) -> list[WorkAttachment]:
        rows = self.session.execute(
            select(work_item_attachments)
            .where(_in_scope(work_item_attachments, context), work_item_attachments.c.work_item_id == item_id)
            .order_by(work_item_attachments.c.created_at, work_item_attachments.c.id)
        ).all()
        return [
            WorkAttachment(id=r.id, file_id=r.file_id, file_name=r.file_name, added_by=r.added_by_user_id, created_at=r.created_at)
            for r in rows
        ]

    def add_attachment(self, context: TenantContext, item_id: str, attachment: WorkAttachment) -> WorkAttachment:
        try:
            with self.session.begin_nested():
                self.session.execute(
                    insert(work_item_attachments).values(
                        **_scope_values(context), id=attachment.id, work_item_id=item_id, file_id=attachment.file_id,
                        file_name=attachment.file_name, added_by_user_id=attachment.added_by, created_at=attachment.created_at,
                    )
                )
        except IntegrityError as exc:
            raise ValueError("This file is already attached") from exc
        return attachment

    def delete_attachment(self, context: TenantContext, item_id: str, attachment_id: str) -> bool:
        result = self.session.execute(
            delete(work_item_attachments).where(
                _in_scope(work_item_attachments, context), work_item_attachments.c.work_item_id == item_id,
                work_item_attachments.c.id == attachment_id,
            )
        )
        return bool(result.rowcount)

    # activity -------------------------------------------------------------------------
    def add_activity(self, context: TenantContext, entry: ActivityEntry) -> ActivityEntry:
        self.session.execute(
            insert(team_activity).values(
                **_scope_values(context), id=entry.id, work_item_id=entry.work_item_id, actor_type=entry.actor_type.value,
                actor_id=entry.actor_id, action=entry.action, summary=entry.summary[:500], created_at=entry.created_at,
            )
        )
        return entry

    def list_activity(
        self, context: TenantContext, *, limit: int, before: str | None, work_item_id: str | None
    ) -> list[ActivityEntry]:
        clauses: list[Any] = [_in_scope(team_activity, context)]
        if work_item_id is not None:
            clauses.append(team_activity.c.work_item_id == work_item_id)
        if before is not None:
            anchor = self.session.execute(
                select(team_activity.c.created_at, team_activity.c.id).where(
                    _in_scope(team_activity, context), team_activity.c.id == before
                )
            ).first()
            if anchor is None:
                raise UnknownCursorError(before)
            clauses.append(tuple_(team_activity.c.created_at, team_activity.c.id) < tuple_(anchor[0], anchor[1]))
        rows = self.session.execute(
            select(team_activity).where(*clauses).order_by(team_activity.c.created_at.desc(), team_activity.c.id.desc()).limit(limit)
        ).all()
        return [
            ActivityEntry(
                id=r.id, work_item_id=r.work_item_id, actor_type=r.actor_type, actor_id=r.actor_id, action=r.action,
                summary=r.summary, created_at=r.created_at,
            )
            for r in rows
        ]

    # notifications --------------------------------------------------------------------
    @staticmethod
    def _notification(row: Any) -> UserNotification:
        data = dict(row._mapping)
        data.pop("tenant_id")
        data.pop("workspace_id")
        return UserNotification(**data)

    def _mine(self, context: TenantContext) -> Any:
        return and_(_in_scope(user_notifications, context), user_notifications.c.user_id == context.user_id)

    def add_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification:
        values = notification.model_dump(mode="python")
        values["email_status"] = notification.email_status.value
        values["push_status"] = notification.push_status.value
        self.session.execute(insert(user_notifications).values(**_scope_values(context), **values))
        return notification

    def list_notifications(
        self, context: TenantContext, *, limit: int, before: str | None, unread_only: bool
    ) -> list[UserNotification]:
        clauses: list[Any] = [self._mine(context)]
        if before is not None:
            anchor = self.session.execute(
                select(user_notifications.c.created_at, user_notifications.c.id).where(
                    self._mine(context), user_notifications.c.id == before
                )
            ).first()
            if anchor is None:
                raise UnknownCursorError(before)
            clauses.append(tuple_(user_notifications.c.created_at, user_notifications.c.id) < tuple_(anchor[0], anchor[1]))
        if unread_only:
            clauses.append(user_notifications.c.read_at.is_(None))
        rows = self.session.execute(
            select(user_notifications)
            .where(*clauses)
            .order_by(user_notifications.c.created_at.desc(), user_notifications.c.id.desc())
            .limit(limit)
        ).all()
        return [self._notification(row) for row in rows]

    def count_unread(self, context: TenantContext) -> int:
        return int(
            self.session.execute(
                select(func.count()).select_from(user_notifications).where(
                    self._mine(context), user_notifications.c.read_at.is_(None)
                )
            ).scalar_one()
        )

    def get_notification(self, context: TenantContext, notification_id: str) -> UserNotification | None:
        row = self.session.execute(
            select(user_notifications).where(self._mine(context), user_notifications.c.id == notification_id)
        ).first()
        return self._notification(row) if row else None

    def save_notification(self, context: TenantContext, notification: UserNotification) -> UserNotification:
        if notification.user_id != context.user_id:
            raise PermissionError("notifications can only be changed by their recipient")
        self.session.execute(
            update(user_notifications)
            .where(self._mine(context), user_notifications.c.id == notification.id)
            .values(
                read_at=notification.read_at, email_status=notification.email_status.value,
                email_attempts=notification.email_attempts, email_error=notification.email_error,
                push_status=notification.push_status.value,
            )
        )
        return notification

    def mark_all_read(self, context: TenantContext, at: datetime) -> int:
        result = self.session.execute(
            update(user_notifications)
            .where(self._mine(context), user_notifications.c.read_at.is_(None))
            .values(read_at=at)
        )
        return int(result.rowcount or 0)
