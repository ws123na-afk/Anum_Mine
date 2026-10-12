"""Team work management (docs/team-work.md).

People assign work to each other, to ANUM and to department assistants; work items have
a status, priority, due date, follow-up time, comments, a checklist and attachments.
Every change is checked server-side (``work:*`` permissions plus who created or is
assigned the item), audited, written to the workspace activity feed and published as a
canonical event, all in the request's transaction. Assignments and reminders notify the
person through the in-app inbox and, by their preferences, email
(``anum_api/notifications.py``).

"ANUM, ask Ahmed to call the supplier by Thursday" works two ways: the Team screen's
command box (``POST /team/commands/parse`` previews, ``POST /team/commands`` creates) and
the governed runtime's ``team.assign_task`` tool, selected for such a sentence in a task,
voice command or automation, so tool policy and approvals apply.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from .agent_tools import ToolCall, ToolDefinition, ToolResult
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .schemas import RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from .scoped_store import ScopeNotProvisionedError
from .settings import settings
from .team_commands import CommandError, match_people, parse_command
from .team_models import (
    ANUM_ASSIGNEE_ID,
    OPEN_STATUSES,
    ActivityEntry,
    ActivityPage,
    ActorType,
    Assignee,
    AssigneeType,
    AttachmentCreate,
    ChecklistCreate,
    ChecklistItem,
    ChecklistUpdate,
    CommandCandidate,
    CommandParse,
    CommandPreview,
    CommentCreate,
    EmailStatus,
    MemberProfile,
    MemberProfileUpdate,
    TeamPerson,
    WorkAttachment,
    WorkComment,
    WorkItem,
    WorkItemCreate,
    WorkItemDetail,
    WorkItemPage,
    WorkItemUpdate,
    WorkItemView,
    Workload,
    WorkloadRow,
    WorkPriority,
    WorkSource,
    WorkStatus,
)
from .team_store import ItemCounts, TeamStore, UnknownCursorError, WorkFilter, memory_team_store

logger = logging.getLogger("anum_api.team_work")

router = APIRouter(prefix="/api/v1", tags=["team"])

TEAM_ASSIGN_TOOL = "team.assign_task"
ANUM_NAME = "ANUM"
WORKLOAD_LIMIT = 5000


# ----------------------------------------------------------------------------- unit of work


class TeamUnit:
    """One transaction: the request repository (audit, events, memberships) and the team store."""

    def __init__(self, context: TenantContext, repository: Any, store: TeamStore) -> None:
        self.context = context
        self.repository = repository
        self.store = store
        # (recipient user id, notification id) whose email is sent after commit.
        self.pending_email: list[tuple[str, str]] = []
        self._names: dict[tuple[str, str], str] | None = None

    @property
    def session(self) -> Any:
        return getattr(self.store, "session", None)

    def require_workspace(self) -> None:
        if self.session is None:
            return
        from .db.scope import require_workspace

        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)

    def file_record(self, file_id: str) -> Any:
        if self.session is None:
            from .files import file_store

            return file_store.get(self.context, file_id)
        from .db.workspace_settings_repository import SqlAlchemyFileMetadataStore

        return SqlAlchemyFileMetadataStore(self.session).get(self.context, file_id)

    # names -----------------------------------------------------------------------------
    def people(self) -> list[TeamPerson]:
        profiles = self.store.list_profiles(self.context)
        memberships = [m for m in self.repository.list_memberships(self.context) if m.active]
        entries = [(m.user_id, m.role) for m in memberships]
        role = next((r for r in self.context.roles if r in {"owner", "member", "viewer"}), None)
        if role and settings.auth_mode != "oidc" and not any(m.user_id == self.context.user_id for m in memberships):
            # Local header mode can act without a persisted membership; show the caller.
            entries.append((self.context.user_id, role))
        out = []
        for user_id, member_role in entries:
            profile = profiles.get(user_id)
            out.append(
                TeamPerson(
                    user_id=user_id,
                    role=member_role,
                    display_name=profile.display_name if profile else user_id,
                    has_profile=profile is not None,
                    email=profile.email if profile else None,
                    department=profile.department if profile else None,
                    job_title=profile.job_title if profile else None,
                    capacity_hours=profile.capacity_hours if profile else None,
                    timezone=profile.timezone if profile else "UTC",
                    assignable=member_role != "viewer",
                )
            )
        return sorted(out, key=lambda p: (p.display_name.casefold(), p.user_id))

    def names(self) -> dict[tuple[str, str], str]:
        if self._names is None:
            names = {("user", p.user_id): p.display_name for p in self.people()}
            names.update({("assistant", a.id): a.name for a in self.store.list_assistants(self.context)})
            names[("anum", ANUM_ASSIGNEE_ID)] = ANUM_NAME
            self._names = names
        return self._names

    def name_of(self, kind: str | None, identifier: str | None) -> str | None:
        if kind is None or identifier is None:
            return None
        return self.names().get((kind, identifier), identifier)

    def timezone_of(self, user_id: str) -> ZoneInfo:
        profile = self.store.list_profiles(self.context).get(user_id)
        try:
            return ZoneInfo(profile.timezone if profile else "UTC")
        except Exception:  # a stored zone removed from tzdata: fall back, never fail
            return ZoneInfo("UTC")

    # logging ---------------------------------------------------------------------------
    def log(
        self,
        action: str,
        summary: str,
        *,
        item_id: str | None,
        event: CanonicalEventName | None = None,
        payload: dict[str, Any] | None = None,
        actor_type: ActorType = ActorType.USER,
        actor_id: str | None = None,
        audit: bool = True,
    ) -> None:
        """Activity feed entry, audit record and canonical event for one change."""
        now = utc_now()
        actor = actor_id or self.context.user_id
        self.store.add_activity(
            self.context,
            ActivityEntry(
                id=new_id("act"), work_item_id=item_id, actor_type=actor_type, actor_id=actor,
                action=action, summary=summary[:500], created_at=now,
            ),
        )
        if audit:
            self.repository.record_audit(
                AuditRecord(
                    id=new_id("audit"), tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id,
                    actor=self.context.user_id, action=action, target=item_id or action, outcome="success",
                    correlation_id=item_id or new_id("corr"), created_at=now,
                    metadata={"actor_type": actor_type.value, "actor_id": actor, **(payload or {})},
                )
            )
        if event is not None:
            self.repository.record_event(
                create_event(event, self.context, item_id or action, payload or {}, created_at=now).event
            )

    def notify(self, user_id: str, kind: str, title: str, body: str, item_id: str | None) -> None:
        from .notifications import create_notification

        profile = self.store.list_profiles(self.context).get(user_id)
        notification = create_notification(
            self.store, self.context, user_id=user_id, kind=kind, title=title, body=body,
            work_item_id=item_id, email=profile.email if profile else None,
        )
        if notification.email_status == EmailStatus.PENDING:
            self.pending_email.append((user_id, notification.id))
        # The recipient's clients refresh their inbox; the payload names nobody else.
        self.repository.record_event(
            create_event(
                CanonicalEventName.NOTIFICATION_CREATED, self.context, notification.id,
                {"recipient": user_id, "kind": kind, "work_item_id": item_id},
            ).event
        )


@contextmanager
def open_team_unit(context: TenantContext, *, require_membership: bool = False) -> Iterator[TeamUnit]:
    """A team unit of work in the caller's RLS context (``anum.user_id`` set too).

    With PostgreSQL the repository and the team store share one session, so the change,
    its audit record, activity, notifications and outbox event commit or roll back
    together; committed events are handed to the event runtime.
    """
    from .dependencies import _unit_of_work

    try:
        with _unit_of_work(context, require_membership=require_membership) as repository:
            session = getattr(repository, "session", None)
            if settings.repository_backend == "postgresql" and session is not None:
                from .db.session import set_tenant_context
                from .db.team_repository import SqlAlchemyTeamStore

                set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
                store: TeamStore = SqlAlchemyTeamStore(session)
            else:
                store = memory_team_store
            yield TeamUnit(context, repository, store)
    except ScopeNotProvisionedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Complete onboarding for this workspace first") from exc


def _deliver_after_commit(background: BackgroundTasks, unit: TeamUnit) -> None:
    if unit.pending_email:
        from .notifications import deliver_pending_email

        background.add_task(deliver_pending_email, unit.context, list(unit.pending_email))


# ----------------------------------------------------------------------------- rules


def _is_admin(context: TenantContext) -> bool:
    return has_permission(context, Permission.WORK_ADMIN)


def can_edit(context: TenantContext, item: WorkItem) -> bool:
    if not has_permission(context, Permission.WORK_MANAGE):
        return False
    if _is_admin(context) or item.created_by == context.user_id:
        return True
    return item.assignee_type == AssigneeType.USER and item.assignee_id == context.user_id


def can_delete(context: TenantContext, item: WorkItem) -> bool:
    return has_permission(context, Permission.WORK_MANAGE) and (_is_admin(context) or item.created_by == context.user_id)


EDIT_DENIED = (
    "Only the person who created this work item, the person it is assigned to, or a "
    "workspace owner can change it."
)
DELETE_DENIED = "Only the person who created this work item or a workspace owner can delete it."


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_manage(context: TenantContext) -> None:
    if not has_permission(context, Permission.WORK_MANAGE):
        raise _forbidden("Viewers can read team work but not change it. Ask a workspace owner to make you a member.")


def resolve_assignee(unit: TeamUnit, assignee: Assignee | None) -> Assignee | None:
    """422 unless the assignee exists in this workspace and can take work."""
    if assignee is None:
        return None
    if assignee.type == AssigneeType.ANUM:
        return assignee
    if assignee.type == AssigneeType.ASSISTANT:
        found = unit.store.get_assistant(unit.context, assignee.id)
        if found is None:
            raise HTTPException(status_code=422, detail="No department assistant with that id in this workspace")
        if not found.enabled:
            raise HTTPException(status_code=422, detail=f"{found.name} is turned off; an owner can turn it on under Assistants")
        return assignee
    person = next((p for p in unit.people() if p.user_id == assignee.id), None)
    if person is None:
        raise HTTPException(status_code=422, detail="That person is not an active member of this workspace")
    if not person.assignable:
        raise HTTPException(
            status_code=422, detail=f"{person.display_name} is a viewer and cannot be assigned work; an owner can change their role"
        )
    return assignee


def _view(unit: TeamUnit, item: WorkItem, counts: ItemCounts | None = None, now: datetime | None = None) -> WorkItemView:
    counts = counts or ItemCounts()
    now = now or utc_now()
    return WorkItemView(
        **item.model_dump(),
        assignee_name=unit.name_of(item.assignee_type.value if item.assignee_type else None, item.assignee_id),
        created_by_name=unit.name_of("user", item.created_by),
        overdue=item.status in OPEN_STATUSES and item.due_at is not None and item.due_at < now,
        checklist_total=counts.checklist_total,
        checklist_done=counts.checklist_done,
        comment_count=counts.comments,
        attachment_count=counts.attachments,
        can_edit=can_edit(unit.context, item),
        can_delete=can_delete(unit.context, item),
    )


def _detail(unit: TeamUnit, item: WorkItem) -> WorkItemDetail:
    checklist = unit.store.list_checklist(unit.context, item.id)
    comments = unit.store.list_comments(unit.context, item.id)
    attachments = unit.store.list_attachments(unit.context, item.id)
    for comment in comments:
        comment.author_name = unit.name_of(comment.author_type.value, comment.author_id)
    for attachment in attachments:
        attachment.available = unit.file_record(attachment.file_id) is not None
    counts = ItemCounts(
        checklist_total=len(checklist), checklist_done=sum(1 for c in checklist if c.done),
        comments=len(comments), attachments=len(attachments),
    )
    return WorkItemDetail(
        **_view(unit, item, counts).model_dump(), checklist=checklist, comments=comments, attachments=attachments
    )


def _load(unit: TeamUnit, item_id: str, *, for_update: bool = False) -> WorkItem:
    item = unit.store.get_item(unit.context, item_id, for_update=for_update)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    return item


def _due_phrase(item: WorkItem, unit: TeamUnit, user_id: str) -> str:
    if item.due_at is None:
        return ""
    local = item.due_at.astimezone(unit.timezone_of(user_id))
    return f" Due {local:%a %d %b %Y %H:%M} ({local.tzname()})."


def notify_assignment(unit: TeamUnit, item: WorkItem, *, by_name: str) -> None:
    if item.assignee_type == AssigneeType.USER and item.assignee_id and item.assignee_id != unit.context.user_id:
        unit.notify(
            item.assignee_id, "work_assigned", f"New work: {item.title}",
            f"{by_name} assigned you \"{item.title}\" ({item.priority.value} priority).{_due_phrase(item, unit, item.assignee_id)}",
            item.id,
        )


def create_item(
    unit: TeamUnit,
    payload: WorkItemCreate,
    *,
    source: WorkSource = WorkSource.MANUAL,
    source_detail: dict[str, Any] | None = None,
    actor_type: ActorType = ActorType.USER,
    actor_id: str | None = None,
) -> WorkItem:
    unit.require_workspace()
    assignee = resolve_assignee(unit, payload.assignee)
    now = utc_now()
    item = WorkItem(
        id=new_id("work"), tenant_id=unit.context.tenant_id, workspace_id=unit.context.workspace_id,
        title=payload.title, description=payload.description, priority=payload.priority,
        assignee_type=assignee.type if assignee else None, assignee_id=assignee.id if assignee else None,
        created_by=unit.context.user_id, due_at=payload.due_at, follow_up_at=payload.follow_up_at,
        estimate_hours=payload.estimate_hours, source=source, source_detail=source_detail or {},
        created_at=now, updated_at=now,
    )
    unit.store.insert_item(unit.context, item)
    for position, label in enumerate(payload.checklist):
        unit.store.save_checklist(
            unit.context, item.id, ChecklistItem(id=new_id("chk"), label=label, position=position, created_at=now)
        )
    by_name = unit.name_of("user", unit.context.user_id) or unit.context.user_id
    if actor_type != ActorType.USER:
        by_name = f"{unit.name_of(actor_type.value, actor_id) or ANUM_NAME} (for {by_name})"
    assignee_name = unit.name_of(item.assignee_type.value, item.assignee_id) if item.assignee_type else None
    summary = f"{by_name} created \"{item.title}\"" + (f" for {assignee_name}" if assignee_name else "")
    unit.log(
        "work_item.create", summary, item_id=item.id, event=CanonicalEventName.WORK_ITEM_CREATED,
        payload={
            "status": item.status.value, "priority": item.priority.value, "source": source.value,
            "assignee_type": item.assignee_type.value if item.assignee_type else None, "assignee_id": item.assignee_id,
        },
        actor_type=actor_type, actor_id=actor_id,
    )
    notify_assignment(unit, item, by_name=by_name)
    return item


# ----------------------------------------------------------------------------- people


@router.get("/team/people", response_model=list[TeamPerson])
def list_people(context: TenantContext = Depends(tenant_context)) -> list[TeamPerson]:
    """Active members of this workspace with their team profiles; every member may read it."""
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        return unit.people()


@router.put("/team/people/{user_id}", response_model=MemberProfile)
def save_profile(user_id: str, payload: MemberProfileUpdate, context: TenantContext = Depends(tenant_context)) -> MemberProfile:
    """Your own team profile, or anyone's for a workspace owner."""
    require_permission(context, Permission.WORK_READ)
    if user_id != context.user_id and not _is_admin(context):
        raise _forbidden("You can edit your own team profile; a workspace owner can edit anyone's.")
    with open_team_unit(context, require_membership=True) as unit:
        unit.require_workspace()
        if not any(p.user_id == user_id for p in unit.people()):
            raise HTTPException(status_code=404, detail="That person is not an active member of this workspace")
        profile = MemberProfile(user_id=user_id, **payload.model_dump(), updated_at=utc_now())
        unit.store.save_profile(context, profile)
        unit.log(
            "team_profile.update", f"{profile.display_name}'s team profile was updated", item_id=None,
            payload={"user_id": user_id},
        )
        return profile


# ----------------------------------------------------------------------------- work items


@router.get("/work-items", response_model=WorkItemPage)
def list_work_items(
    scope: str = Query(default="all", pattern="^(all|mine|created|anum)$"),
    status_filter: str = Query(default="open", alias="status", pattern="^(open|all|todo|in_progress|blocked|done|cancelled)$"),
    priority: WorkPriority | None = None,
    assignee_type: AssigneeType | None = None,
    assignee_id: str | None = Query(default=None, max_length=160),
    overdue: bool = False,
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="due", pattern="^(due|updated|priority)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> WorkItemPage:
    """Work items of this workspace. ``scope=mine`` is "My tasks"; ``anum`` is work for ANUM and assistants."""
    require_permission(context, Permission.WORK_READ)
    now = utc_now()
    flt = WorkFilter(priority=priority, query=q.strip() if q and q.strip() else None, sort=sort)
    if status_filter == "open":
        flt.statuses = frozenset(OPEN_STATUSES)
    elif status_filter != "all":
        flt.statuses = frozenset({WorkStatus(status_filter)})
    if scope == "mine":
        flt.assignee_type, flt.assignee_id = AssigneeType.USER, context.user_id
    elif scope == "created":
        flt.created_by = context.user_id
    if assignee_type is not None:
        flt.assignee_type = assignee_type
        if assignee_id:
            flt.assignee_id = ANUM_ASSIGNEE_ID if assignee_type == AssigneeType.ANUM else assignee_id
    if overdue:
        flt.overdue_at = now
    with open_team_unit(context, require_membership=True) as unit:
        if scope == "anum":
            items, total = _query_anum(unit, flt, limit, offset)
        else:
            items, total = unit.store.query_items(context, flt, limit, offset)
        counts = unit.store.item_counts(context, [i.id for i in items])
        views = [_view(unit, item, counts.get(item.id), now) for item in items]
    next_offset = offset + len(views) if offset + len(views) < total else None
    return WorkItemPage(items=views, total=total, limit=limit, offset=offset, next_offset=next_offset)


def _query_anum(unit: TeamUnit, flt: WorkFilter, limit: int, offset: int) -> tuple[list[WorkItem], int]:
    """Work for ANUM and for every department assistant, merged in the requested order."""
    from .team_store import sort_key

    collected: list[WorkItem] = []
    totals = 0
    for kind in (AssigneeType.ANUM, AssigneeType.ASSISTANT):
        part = WorkFilter(**{**flt.__dict__, "assignee_type": kind, "assignee_id": None})
        items, total = unit.store.query_items(unit.context, part, offset + limit, 0)
        collected.extend(items)
        totals += total
    collected.sort(key=sort_key(flt.sort))
    return collected[offset : offset + limit], totals


@router.post("/work-items", response_model=WorkItemDetail, status_code=status.HTTP_201_CREATED)
def create_work_item(
    payload: WorkItemCreate, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)
) -> WorkItemDetail:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = create_item(unit, payload)
        detail = _detail(unit, item)
    _deliver_after_commit(background, unit)
    return detail


@router.get("/work-items/workload", response_model=Workload)
def workload(context: TenantContext = Depends(tenant_context)) -> Workload:
    """Open work per person, ANUM and assistant: counts, overdue, due this week and load against capacity."""
    require_permission(context, Permission.WORK_READ)
    now = utc_now()
    week_end = now + timedelta(days=7)
    with open_team_unit(context, require_membership=True) as unit:
        items = unit.store.open_items(context, WORKLOAD_LIMIT)
        people = unit.people()
        rows: dict[tuple[str, str], WorkloadRow] = {
            ("user", p.user_id): WorkloadRow(
                assignee_type=AssigneeType.USER, assignee_id=p.user_id, name=p.display_name, capacity_hours=p.capacity_hours
            )
            for p in people
            if p.assignable
        }
        unassigned = 0
        for item in items:
            if item.assignee_type is None or item.assignee_id is None:
                unassigned += 1
                continue
            key = (item.assignee_type.value, item.assignee_id)
            row = rows.get(key)
            if row is None:
                row = rows[key] = WorkloadRow(
                    assignee_type=item.assignee_type, assignee_id=item.assignee_id,
                    name=unit.name_of(*key) or item.assignee_id,
                )
            row.open += 1
            row.in_progress += item.status == WorkStatus.IN_PROGRESS
            row.blocked += item.status == WorkStatus.BLOCKED
            if item.due_at is not None:
                row.overdue += item.due_at < now
                row.due_this_week += now <= item.due_at <= week_end
            row.estimate_hours += item.estimate_hours or 0.0
    for row in rows.values():
        row.estimate_hours = round(row.estimate_hours, 2)
        if row.capacity_hours:
            row.load_percent = round(100 * row.estimate_hours / row.capacity_hours)
    ordered = sorted(rows.values(), key=lambda r: (-r.overdue, -r.open, r.name.casefold()))
    return Workload(rows=ordered, unassigned_open=unassigned, generated_at=now)


@router.get("/work-items/{item_id}", response_model=WorkItemDetail)
def get_work_item(item_id: str, context: TenantContext = Depends(tenant_context)) -> WorkItemDetail:
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        return _detail(unit, _load(unit, item_id))


@router.patch("/work-items/{item_id}", response_model=WorkItemDetail)
def update_work_item(
    item_id: str, payload: WorkItemUpdate, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)
) -> WorkItemDetail:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _load(unit, item_id, for_update=True)
        if not can_edit(context, item):
            raise _forbidden(EDIT_DENIED)
        if payload.version != item.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Someone changed this work item after you opened it. Reload it to see their change, then try again.",
            )
        changes = _apply_update(unit, item, payload)
        if not changes:
            return _detail(unit, item)
        previous = item.version
        item.version += 1
        item.updated_at = utc_now()
        if not unit.store.update_item(context, item, previous):
            raise HTTPException(status_code=409, detail="Someone changed this work item at the same time. Reload it and try again.")
        detail = _detail(unit, item)
    _deliver_after_commit(background, unit)
    return detail


def _apply_update(unit: TeamUnit, item: WorkItem, payload: WorkItemUpdate) -> list[str]:
    """Apply ``payload`` to ``item``; log, notify and return the names of changed fields."""
    context = unit.context
    changes: list[str] = []
    by_name = unit.name_of("user", context.user_id) or context.user_id
    old_assignee = (item.assignee_type, item.assignee_id)
    if payload.title is not None and payload.title != item.title:
        item.title, changes = payload.title, [*changes, "title"]
    if payload.description is not None and payload.description != item.description:
        item.description, changes = payload.description, [*changes, "description"]
    if payload.priority is not None and payload.priority != item.priority:
        item.priority, changes = payload.priority, [*changes, "priority"]
    if payload.estimate_hours is not None and payload.estimate_hours != item.estimate_hours:
        item.estimate_hours, changes = payload.estimate_hours, [*changes, "estimate_hours"]
    if payload.clear_due and item.due_at is not None:
        item.due_at, item.reminder_sent_at, changes = None, None, [*changes, "due_at"]
    elif payload.due_at is not None and payload.due_at != item.due_at:
        # A new due date earns a new overdue reminder.
        item.due_at, item.reminder_sent_at, changes = payload.due_at, None, [*changes, "due_at"]
    if payload.clear_follow_up and item.follow_up_at is not None:
        item.follow_up_at, changes = None, [*changes, "follow_up_at"]
    elif payload.follow_up_at is not None and payload.follow_up_at != item.follow_up_at:
        item.follow_up_at, changes = payload.follow_up_at, [*changes, "follow_up_at"]
    if payload.clear_assignee and item.assignee_type is not None:
        item.assignee_type, item.assignee_id, changes = None, None, [*changes, "assignee"]
    elif payload.assignee is not None and (payload.assignee.type, payload.assignee.id) != old_assignee:
        resolve_assignee(unit, payload.assignee)
        item.assignee_type, item.assignee_id = payload.assignee.type, payload.assignee.id
        changes.append("assignee")
    new_status = payload.status
    status_changed = new_status is not None and new_status != item.status
    if new_status is not None and status_changed:
        item.status = new_status
        item.completed_at = utc_now() if new_status == WorkStatus.DONE else None
        changes.append("status")
    if not changes:
        return changes
    payload_fields: dict[str, Any] = {"changed": changes, "status": item.status.value, "version": item.version + 1}
    if "assignee" in changes:
        assignee_name = unit.name_of(item.assignee_type.value, item.assignee_id) if item.assignee_type else None
        unit.log(
            "work_item.assign",
            f"{by_name} assigned \"{item.title}\" to {assignee_name}" if assignee_name else f"{by_name} unassigned \"{item.title}\"",
            item_id=item.id, event=CanonicalEventName.WORK_ITEM_ASSIGNED,
            payload={"assignee_type": item.assignee_type.value if item.assignee_type else None, "assignee_id": item.assignee_id},
        )
        notify_assignment(unit, item, by_name=by_name)
    if status_changed and item.status == WorkStatus.DONE:
        unit.log("work_item.complete", f"{by_name} completed \"{item.title}\"", item_id=item.id,
                 event=CanonicalEventName.WORK_ITEM_COMPLETED, payload=payload_fields)
        if item.created_by != context.user_id:
            unit.notify(item.created_by, "work_completed", f"Done: {item.title}", f"{by_name} marked \"{item.title}\" as done.", item.id)
    else:
        labels = [
            f"status to {item.status.value.replace('_', ' ')}" if c == "status" else c.replace("_", " ")
            for c in changes
            if c != "assignee"
        ]
        if labels:
            unit.log("work_item.update", f"{by_name} changed {', '.join(labels)} on \"{item.title}\"", item_id=item.id,
                     event=CanonicalEventName.WORK_ITEM_UPDATED, payload=payload_fields)
            if status_changed and item.status == WorkStatus.BLOCKED and item.created_by != context.user_id:
                unit.notify(item.created_by, "work_blocked", f"Blocked: {item.title}", f"{by_name} marked \"{item.title}\" as blocked.", item.id)
    return changes


@router.delete("/work-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_work_item(item_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _load(unit, item_id, for_update=True)
        if not can_delete(context, item):
            raise _forbidden(DELETE_DENIED)
        unit.store.delete_item(context, item_id)
        by_name = unit.name_of("user", context.user_id) or context.user_id
        unit.log("work_item.delete", f"{by_name} deleted \"{item.title}\"", item_id=item.id,
                 event=CanonicalEventName.WORK_ITEM_DELETED, payload={})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _touch(unit: TeamUnit, item: WorkItem) -> None:
    """Bump the item's version and timestamp after a change to its parts."""
    previous = item.version
    item.version += 1
    item.updated_at = utc_now()
    if not unit.store.update_item(unit.context, item, previous):
        raise HTTPException(status_code=409, detail="Someone changed this work item at the same time. Reload it and try again.")


# comments ----------------------------------------------------------------------------


@router.post("/work-items/{item_id}/comments", response_model=WorkComment, status_code=status.HTTP_201_CREATED)
def add_comment(
    item_id: str, payload: CommentCreate, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)
) -> WorkComment:
    """Any member may comment on any work item of the workspace; viewers may not."""
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _load(unit, item_id, for_update=True)
        comment = WorkComment(
            id=new_id("cmt"), author_type=ActorType.USER, author_id=context.user_id, body=payload.body, created_at=utc_now()
        )
        unit.store.add_comment(context, item_id, comment)
        _touch(unit, item)
        by_name = unit.name_of("user", context.user_id) or context.user_id
        comment.author_name = by_name
        unit.log("work_item.comment", f"{by_name} commented on \"{item.title}\"", item_id=item.id,
                 event=CanonicalEventName.WORK_ITEM_COMMENTED, payload={"comment_id": comment.id})
        snippet = payload.body if len(payload.body) <= 160 else payload.body[:157] + "..."
        recipients = {item.created_by}
        if item.assignee_type == AssigneeType.USER and item.assignee_id:
            recipients.add(item.assignee_id)
        for user_id in sorted(recipients - {context.user_id}):
            unit.notify(user_id, "work_comment", f"Comment on {item.title}", f"{by_name}: {snippet}", item.id)
    _deliver_after_commit(background, unit)
    return comment


@router.delete("/work-items/{item_id}/comments/{comment_id}", status_code=204, response_model=None)
def delete_comment(item_id: str, comment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _load(unit, item_id, for_update=True)
        comment = next((c for c in unit.store.list_comments(context, item_id) if c.id == comment_id), None)
        if comment is None:
            raise HTTPException(status_code=404, detail="Comment not found")
        if not (_is_admin(context) or (comment.author_type == ActorType.USER and comment.author_id == context.user_id)):
            raise _forbidden("Only the comment's author or a workspace owner can delete it.")
        unit.store.delete_comment(context, item_id, comment_id)
        _touch(unit, item)
        unit.log("work_item.comment_delete", f"A comment on \"{item.title}\" was deleted", item_id=item.id,
                 payload={"comment_id": comment_id})
    return Response(status_code=204)


# checklist ---------------------------------------------------------------------------


def _editable(unit: TeamUnit, item_id: str) -> WorkItem:
    item = _load(unit, item_id, for_update=True)
    if not can_edit(unit.context, item):
        raise _forbidden(EDIT_DENIED)
    return item


@router.post("/work-items/{item_id}/checklist", response_model=ChecklistItem, status_code=201)
def add_checklist(item_id: str, payload: ChecklistCreate, context: TenantContext = Depends(tenant_context)) -> ChecklistItem:
    _require_manage(context)
    label = " ".join(payload.label.split())
    if not label:
        raise HTTPException(status_code=422, detail="A checklist item needs some text")
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        existing = unit.store.list_checklist(context, item_id)
        if len(existing) >= 100:
            raise HTTPException(status_code=422, detail="A work item can have at most 100 checklist items")
        entry = ChecklistItem(
            id=new_id("chk"), label=label, position=max((c.position for c in existing), default=-1) + 1, created_at=utc_now()
        )
        unit.store.save_checklist(context, item_id, entry)
        _touch(unit, item)
        unit.log("work_item.checklist_add", f"Checklist item added to \"{item.title}\"", item_id=item.id, audit=False)
        return entry


@router.patch("/work-items/{item_id}/checklist/{entry_id}", response_model=ChecklistItem)
def update_checklist(
    item_id: str, entry_id: str, payload: ChecklistUpdate, context: TenantContext = Depends(tenant_context)
) -> ChecklistItem:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        entry = next((c for c in unit.store.list_checklist(context, item_id) if c.id == entry_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        if payload.label is not None:
            label = " ".join(payload.label.split())
            if not label:
                raise HTTPException(status_code=422, detail="A checklist item needs some text")
            entry.label = label
        if payload.done is not None and payload.done != entry.done:
            entry.done = payload.done
            entry.done_by, entry.done_at = (context.user_id, utc_now()) if payload.done else (None, None)
            by_name = unit.name_of("user", context.user_id) or context.user_id
            unit.log(
                "work_item.checklist_done" if payload.done else "work_item.checklist_reopen",
                f"{by_name} {'ticked' if payload.done else 'unticked'} \"{entry.label}\" on \"{item.title}\"",
                item_id=item.id, audit=False,
            )
        unit.store.save_checklist(context, item_id, entry)
        _touch(unit, item)
        return entry


@router.delete("/work-items/{item_id}/checklist/{entry_id}", status_code=204, response_model=None)
def delete_checklist(item_id: str, entry_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        if not unit.store.delete_checklist(context, item_id, entry_id):
            raise HTTPException(status_code=404, detail="Checklist item not found")
        _touch(unit, item)
    return Response(status_code=204)


# attachments -------------------------------------------------------------------------


@router.post("/work-items/{item_id}/attachments", response_model=WorkAttachment, status_code=201)
def add_attachment(item_id: str, payload: AttachmentCreate, context: TenantContext = Depends(tenant_context)) -> WorkAttachment:
    """Attach a file already uploaded to this workspace's Files (``POST /api/v1/files``)."""
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        record = unit.file_record(payload.file_id)
        if record is None:
            raise HTTPException(status_code=422, detail="No file with that id in this workspace; upload it under Files first")
        if len(unit.store.list_attachments(context, item_id)) >= 50:
            raise HTTPException(status_code=422, detail="A work item can have at most 50 attachments")
        attachment = WorkAttachment(
            id=new_id("att"), file_id=record.id, file_name=record.name, added_by=context.user_id, created_at=utc_now()
        )
        try:
            unit.store.add_attachment(context, item_id, attachment)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _touch(unit, item)
        by_name = unit.name_of("user", context.user_id) or context.user_id
        unit.log("work_item.attach", f"{by_name} attached {record.name} to \"{item.title}\"", item_id=item.id,
                 payload={"file_id": record.id})
        return attachment


@router.delete("/work-items/{item_id}/attachments/{attachment_id}", status_code=204, response_model=None)
def delete_attachment(item_id: str, attachment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Detaches the file from the work item; the file itself stays in Files."""
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        if not unit.store.delete_attachment(context, item_id, attachment_id):
            raise HTTPException(status_code=404, detail="Attachment not found")
        _touch(unit, item)
        unit.log("work_item.detach", f"An attachment was removed from \"{item.title}\"", item_id=item.id,
                 payload={"attachment_id": attachment_id})
    return Response(status_code=204)


# hand to ANUM ----------------------------------------------------------------------------


class HandOffResult(BaseModel):
    work_item: WorkItemDetail
    task: Task


@router.post("/work-items/{item_id}/hand-off", response_model=HandOffResult)
def hand_off(item_id: str, context: TenantContext = Depends(tenant_context)) -> HandOffResult:
    """Create the agent task for work assigned to ANUM or an assistant.

    The client then runs it with ``POST /api/v1/tasks/{task_id}/run``: the run goes
    through the normal runtime, tool policy and approvals; an assistant's task is
    narrowed to that assistant's allowed tools and autonomy.
    """
    require_permission(context, Permission.TASK_CREATE)
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        item = _editable(unit, item_id)
        if item.assignee_type not in {AssigneeType.ANUM, AssigneeType.ASSISTANT}:
            raise HTTPException(status_code=422, detail="Assign this work to ANUM or a department assistant first")
        if item.status not in OPEN_STATUSES:
            raise HTTPException(status_code=422, detail="This work item is closed; reopen it to hand it off again")
        assistant = None
        if item.assignee_type == AssigneeType.ASSISTANT:
            assistant = unit.store.get_assistant(context, item.assignee_id or "")
            if assistant is None or not assistant.enabled:
                raise HTTPException(status_code=422, detail="That assistant is missing or turned off")
        lines = [item.title]
        if item.description:
            lines.append(item.description)
        if item.due_at:
            lines.append(f"Due: {item.due_at.isoformat()}")
        now = utc_now()
        task = Task(
            id=new_id("task"), title=item.title[:160], prompt="\n\n".join(lines)[:8000], status=TaskStatus.CREATED,
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now,
            created_by=context.user_id, assistant_id=assistant.id if assistant else None,
        )
        unit.repository.create_task(task)
        unit.repository.record_event(
            create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event
        )
        item.agent_task_id = task.id
        if item.status == WorkStatus.TODO:
            item.status = WorkStatus.IN_PROGRESS
        _touch(unit, item)
        who = assistant.name if assistant else ANUM_NAME
        unit.log(
            "work_item.hand_off", f"{who} started on \"{item.title}\"", item_id=item.id,
            event=CanonicalEventName.WORK_ITEM_UPDATED, payload={"task_id": task.id, "status": item.status.value},
            actor_type=ActorType.ASSISTANT if assistant else ActorType.ANUM, actor_id=assistant.id if assistant else ANUM_ASSIGNEE_ID,
        )
        return HandOffResult(work_item=_detail(unit, item), task=task)


# ----------------------------------------------------------------------------- activity


@router.get("/team/activity", response_model=ActivityPage)
def team_activity(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=80),
    work_item_id: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> ActivityPage:
    """The workspace's activity feed (people, ANUM and assistants), newest first."""
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        try:
            rows = unit.store.list_activity(context, limit=limit + 1, before=cursor, work_item_id=work_item_id)
        except UnknownCursorError as exc:
            raise HTTPException(status_code=400, detail="Unknown cursor; reload the feed") from exc
        for row in rows:
            row.actor_name = unit.name_of(row.actor_type.value, row.actor_id)
    more = len(rows) > limit
    rows = rows[:limit]
    return ActivityPage(items=rows, next_cursor=rows[-1].id if more and rows else None)


# ----------------------------------------------------------------------------- commands


def _candidates(unit: TeamUnit) -> list[tuple[str, str, str]]:
    people = [("user", p.user_id, p.display_name) for p in unit.people() if p.assignable]
    assistants = [("assistant", a.id, a.name) for a in unit.store.list_assistants(unit.context) if a.enabled]
    return [*people, *assistants, ("anum", ANUM_ASSIGNEE_ID, ANUM_NAME)]


def preview_command(unit: TeamUnit, text: str, zone_name: str | None) -> CommandPreview:
    zone = ZoneInfo(zone_name) if zone_name else unit.timezone_of(unit.context.user_id)
    try:
        parsed = parse_command(text, utc_now(), zone)
    except CommandError as exc:
        return CommandPreview(understood=False, problem=str(exc))
    matches = [CommandCandidate(type=AssigneeType(t), id=i, name=n) for t, i, n in match_people(parsed.person, _candidates(unit))]
    preview = CommandPreview(
        understood=len(matches) == 1, person=parsed.person, title=parsed.work, due_at=parsed.due_at,
        due_text=parsed.due_text, candidates=matches, assignee=matches[0] if len(matches) == 1 else None,
    )
    if not matches:
        preview.problem = (
            f"Nobody in this workspace is called {parsed.person}. Add their name to their team profile "
            "under Team, People, or pick the person below."
        )
    elif len(matches) > 1:
        preview.problem = f"More than one person matches {parsed.person}; pick who you mean."
    return preview


class CommandCreate(CommandParse):
    model_config = ConfigDict(extra="forbid")

    # Picks among several candidates (or overrides the match) from the preview.
    assignee: Assignee | None = None
    priority: WorkPriority = WorkPriority.NORMAL


@router.post("/team/commands/parse", response_model=CommandPreview)
def parse_team_command(payload: CommandParse, context: TenantContext = Depends(tenant_context)) -> CommandPreview:
    """What ANUM understood from the sentence; creates nothing."""
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        return preview_command(unit, payload.text, payload.timezone)


@router.post("/team/commands", response_model=WorkItemDetail, status_code=201)
def run_team_command(
    payload: CommandCreate, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)
) -> WorkItemDetail:
    """Create the assignment the sentence describes (after the user saw the preview)."""
    _require_manage(context)
    with open_team_unit(context, require_membership=True) as unit:
        preview = preview_command(unit, payload.text, payload.timezone)
        if preview.title is None:
            raise HTTPException(status_code=422, detail=preview.problem or "ANUM could not read that sentence")
        assignee = payload.assignee or (Assignee(type=preview.assignee.type, id=preview.assignee.id) if preview.assignee else None)
        if assignee is None:
            raise HTTPException(status_code=422, detail=preview.problem or "Pick who should do this")
        item = create_item(
            unit,
            WorkItemCreate(title=preview.title, assignee=assignee, due_at=preview.due_at, priority=payload.priority),
            source=WorkSource.COMMAND, source_detail={"command": payload.text[:500]},
            actor_type=ActorType.ANUM, actor_id=ANUM_ASSIGNEE_ID,
        )
        detail = _detail(unit, item)
    _deliver_after_commit(background, unit)
    return detail


# ----------------------------------------------------------------------------- runtime tool

TEAM_ASSIGN_DEFINITION = ToolDefinition(
    name=TEAM_ASSIGN_TOOL,
    description=(
        "Create a work item assigned to a teammate, ANUM or a department assistant from the "
        "user's own sentence, and notify the assignee. Internal to the workspace."
    ),
    # It creates work for a person and notifies them: the workspace's medium-risk policy
    # decides whether that needs an approval (docs/approvals-and-risk.md).
    risk_level=RiskLevel.MEDIUM,
    required_roles=frozenset({"owner", "member"}),
    timeout_seconds=30,
    # Autonomy (docs/autonomy.md): it creates a work item inside the workspace.
    category="tasks",
    action_kind="create",
)


async def assign_from_command(call: ToolCall, context: TenantContext) -> ToolResult:
    """The ``team.assign_task`` handler: parse, resolve, create and notify in one unit of work."""
    command = str(call.arguments.get("command", ""))[:500]
    return await asyncio.to_thread(_assign_sync, command, context)


def _assign_sync(command: str, context: TenantContext) -> ToolResult:
    if not has_permission(context, Permission.WORK_MANAGE):
        return ToolResult(status="blocked", summary="Viewers cannot assign work.")
    try:
        with open_team_unit(context) as unit:
            preview = preview_command(unit, command, None)
            if not preview.understood or preview.assignee is None or preview.title is None:
                return ToolResult(
                    status="failed",
                    summary=(preview.problem or "ANUM could not tell who should do this.")
                    + (" Candidates: " + ", ".join(c.name for c in preview.candidates) if preview.candidates else ""),
                    audit_metadata={"outcome": "unresolved", "candidates": len(preview.candidates)},
                )
            item = create_item(
                unit,
                WorkItemCreate(title=preview.title, assignee=Assignee(type=preview.assignee.type, id=preview.assignee.id), due_at=preview.due_at),
                source=WorkSource.COMMAND, source_detail={"command": command},
                actor_type=ActorType.ANUM, actor_id=ANUM_ASSIGNEE_ID,
            )
            pending = list(unit.pending_email)
            due = ""
            if item.due_at:
                local = item.due_at.astimezone(unit.timezone_of(context.user_id))
                due = f", due {local:%a %d %b %H:%M}"
            summary = f"Assigned \"{item.title}\" to {preview.assignee.name}{due}. They have been notified."
    except HTTPException as exc:
        return ToolResult(status="failed", summary=str(exc.detail))
    if pending:
        from .notifications import deliver_pending_email

        deliver_pending_email(context, pending)
    return ToolResult(
        status="succeeded", summary=summary,
        output={"work_item_id": item.id, "assignee_type": item.assignee_type.value if item.assignee_type else None,
                "assignee_id": item.assignee_id, "due_at": item.due_at.isoformat() if item.due_at else None},
        audit_metadata={"outcome": "assigned", "work_item_id": item.id},
    )


# ----------------------------------------------------------------------------- reminders


def _due_scopes_sql(batch: int) -> list[tuple[str, str, str]]:
    from sqlalchemy import text

    from .maintenance import default_session_factory, discover

    rows = discover(
        default_session_factory,
        text(
            "select id, tenant_id, workspace_id from work_items "
            "where status in ('todo', 'in_progress', 'blocked') "
            "and ((due_at <= now() and reminder_sent_at is null) or follow_up_at <= now()) "
            "order by coalesce(follow_up_at, due_at) limit :batch"
        ),
        {"batch": batch},
    )
    return [(row.tenant_id, row.workspace_id, row.id) for row in rows]


def _pending_email_sql(batch: int) -> list[tuple[str, str, str, str]]:
    from sqlalchemy import text

    from .maintenance import default_session_factory, discover

    rows = discover(
        default_session_factory,
        text(
            "select id, tenant_id, workspace_id, user_id from user_notifications "
            "where email_status = 'pending' and email_attempts < 3 limit :batch"
        ),
        {"batch": batch},
    )
    return [(row.tenant_id, row.workspace_id, row.user_id, row.id) for row in rows]


SYSTEM_USER = "anum"


def remind_item(context: TenantContext, item_id: str, now: datetime | None = None) -> list[str]:
    """Send the overdue reminder and/or ANUM follow-up one item is due for; returns what was sent."""
    now = now or utc_now()
    sent: list[str] = []
    with open_team_unit(context) as unit:
        item = unit.store.get_item(context, item_id, for_update=True)
        if item is None or item.status not in OPEN_STATUSES:
            return sent
        targets = {item.created_by}
        if item.assignee_type == AssigneeType.USER and item.assignee_id:
            targets.add(item.assignee_id)
        assignee = unit.name_of(item.assignee_type.value, item.assignee_id) if item.assignee_type else "nobody"
        if item.due_at is not None and item.due_at <= now and item.reminder_sent_at is None:
            for user_id in sorted(targets):
                local = item.due_at.astimezone(unit.timezone_of(user_id))
                unit.notify(
                    user_id, "work_overdue", f"Overdue: {item.title}",
                    f"\"{item.title}\" (assigned to {assignee}) was due {local:%a %d %b %H:%M}. Update its status or due date.",
                    item.id,
                )
            item.reminder_sent_at = now
            unit.log("work_item.remind", f"ANUM sent an overdue reminder for \"{item.title}\"", item_id=item.id,
                     event=CanonicalEventName.WORK_ITEM_REMINDED, payload={"kind": "overdue"},
                     actor_type=ActorType.ANUM, actor_id=ANUM_ASSIGNEE_ID)
            sent.append("overdue")
        if item.follow_up_at is not None and item.follow_up_at <= now:
            body = f"Following up on \"{item.title}\": how is it going?" + (
                f" It is due {item.due_at:%Y-%m-%d}." if item.due_at else ""
            )
            unit.store.add_comment(
                context, item.id, WorkComment(id=new_id("cmt"), author_type=ActorType.ANUM, author_id=ANUM_ASSIGNEE_ID, body=body, created_at=now)
            )
            for user_id in sorted(targets):
                unit.notify(user_id, "work_follow_up", f"ANUM follow-up: {item.title}", body, item.id)
            item.follow_up_at = None
            unit.log("work_item.follow_up", f"ANUM followed up on \"{item.title}\"", item_id=item.id,
                     event=CanonicalEventName.WORK_ITEM_REMINDED, payload={"kind": "follow_up"},
                     actor_type=ActorType.ANUM, actor_id=ANUM_ASSIGNEE_ID)
            sent.append("follow_up")
        if sent:
            _touch(unit, item)
        pending = list(unit.pending_email)
    if pending:
        from .notifications import deliver_pending_email

        deliver_pending_email(context, pending)
    return sent


def _system_context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])


def run_reminder_pass(batch: int | None = None, now: datetime | None = None) -> dict[str, int]:
    """One sweep: overdue reminders, follow-ups and email retries in every tenant.

    With PostgreSQL the due rows are discovered as ``anum_maintenance`` (ids only) and
    each one is handled in its own transaction inside that tenant's RLS context.
    """
    batch = batch or settings.team_reminder_batch_size
    now = now or utc_now()
    counts = {"reminded": 0, "followed_up": 0, "emails": 0}
    if settings.repository_backend == "postgresql":
        due = _due_scopes_sql(batch)
        pending = _pending_email_sql(batch)
    else:
        due = []
        for tenant_id, workspace_id in memory_team_store.scopes():
            ctx = _system_context(tenant_id, workspace_id)
            flt = WorkFilter(statuses=frozenset(OPEN_STATUSES), sort="due")
            items, _ = memory_team_store.query_items(ctx, flt, 100_000, 0)
            due += [
                (tenant_id, workspace_id, i.id)
                for i in items
                if (i.due_at is not None and i.due_at <= now and i.reminder_sent_at is None)
                or (i.follow_up_at is not None and i.follow_up_at <= now)
            ]
        pending = memory_team_store.pending_email()
    for tenant_id, workspace_id, item_id in due[:batch]:
        try:
            sent = remind_item(_system_context(tenant_id, workspace_id), item_id, now)
        except Exception:
            logger.exception("Team reminder failed", extra={"anum_work_item_id": item_id})
            continue
        counts["reminded"] += "overdue" in sent
        counts["followed_up"] += "follow_up" in sent
    for tenant_id, workspace_id, user_id, notification_id in pending[:batch]:
        from .notifications import deliver_pending_email

        recipient = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=[])
        deliver_pending_email(recipient, [(user_id, notification_id)])
        counts["emails"] += 1
    return counts


class TeamReminderScheduler:
    """Background loop for :func:`run_reminder_pass` (enabled with the automation scheduler).

    Each pass runs in a worker thread; ``stop`` lets a pass in progress finish its
    transactions instead of cancelling it mid-commit.
    """

    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._pass: asyncio.Future[Any] | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-team-reminders")

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
            except Exception:
                logger.exception("Team reminder loop stopped with an error")
        if self._pass is not None and not self._pass.done():
            await asyncio.wait({self._pass})
        self._pass = None

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Team reminder loop started without a stop event")
        while not stop.is_set():
            self._pass = asyncio.ensure_future(asyncio.to_thread(run_reminder_pass))
            try:
                await asyncio.shield(self._pass)
            except Exception:
                logger.exception("Team reminder pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_team_reminder_scheduler(config: Any) -> TeamReminderScheduler | None:
    if not config.automation_scheduler_enabled:
        return None
    return TeamReminderScheduler(config.team_reminder_poll_seconds)
