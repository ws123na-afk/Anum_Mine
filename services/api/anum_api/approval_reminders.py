"""Approval-ageing reminder (docs/approvals-and-risk.md#approval-ageing-reminder).

A workspace owner chooses after how many hours (1 to 23; an approval expires after 24) a
pending approval reminds the people who can approve it, or turns the reminder off (the
default). Once an approval has waited that long and is still pending and not expired, every
active owner of the workspace gets one in-app notification (kind ``approval_reminder``):
the action, its risk, how long it has waited and when it expires. In-app only, never an
email or a phone push. Each approval reminds once: ``approval_reminders`` records it, claimed
with ``insert ... on conflict do nothing`` so two passes never send it twice.

The reminder pass runs with the team reminder sweep (``team_work.run_reminder_pass``). With
PostgreSQL due approvals are discovered as ``anum_maintenance`` (ids and timestamps only,
migration ``0128_setup_plan_gaps``) and each is handled in its own transaction inside its
tenant's RLS context. Nothing here approves, rejects or changes an approval.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import dependencies as deps
from .audit import AuditRecord
from .authorization import Permission
from .schemas import ApprovalStatus, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings

logger = logging.getLogger(__name__)

NOTICE_KIND = "approval_reminder"
SYSTEM_USER = "anum"
SET_DENIED = "Only a workspace owner can change when waiting approvals send a reminder."


class ReminderSettings(BaseModel):
    reminder_hours: int | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class ReminderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_hours: int | None = Field(default=None, ge=1, le=23, description="Hours a pending approval waits before its reminder; null turns it off")


class ReminderStore(Protocol):
    def get(self, context: TenantContext) -> ReminderSettings: ...
    def save(self, context: TenantContext, value: ReminderSettings) -> ReminderSettings: ...
    def claim(self, context: TenantContext, approval_id: str, now: datetime) -> bool: ...
    def set_recipients(self, context: TenantContext, approval_id: str, count: int) -> None: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryReminderStore:
    def __init__(self) -> None:
        self.settings: dict[tuple[str, str], ReminderSettings] = {}
        self.sent: dict[tuple[str, str, str], int] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.settings.clear()
            self.sent.clear()

    def get(self, context: TenantContext) -> ReminderSettings:
        with self._lock:
            return (self.settings.get((context.tenant_id, context.workspace_id)) or ReminderSettings()).model_copy()

    def save(self, context: TenantContext, value: ReminderSettings) -> ReminderSettings:
        with self._lock:
            key = (context.tenant_id, context.workspace_id)
            if value.reminder_hours is None:
                self.settings.pop(key, None)
            else:
                self.settings[key] = value.model_copy()
            return value

    def claim(self, context: TenantContext, approval_id: str, now: datetime) -> bool:
        key = (context.tenant_id, context.workspace_id, approval_id)
        with self._lock:
            if key in self.sent:
                return False
            self.sent[key] = 0
            return True

    def set_recipients(self, context: TenantContext, approval_id: str, count: int) -> None:
        with self._lock:
            self.sent[(context.tenant_id, context.workspace_id, approval_id)] = count

    def scopes(self) -> list[tuple[str, str, int]]:
        with self._lock:
            return [(t, w, s.reminder_hours) for (t, w), s in self.settings.items() if s.reminder_hours]

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from .dependencies import memory_repository

        return memory_repository.record_audit(record)


memory_reminder_store = InMemoryReminderStore()


class SqlReminderStore:
    def __init__(self, session: Any) -> None:
        from .db.repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    def get(self, context: TenantContext) -> ReminderSettings:
        row = self.session.execute(text(
            "select reminder_hours, updated_by, updated_at from approval_reminder_settings where tenant_id = :t and workspace_id = :w"),
            {"t": context.tenant_id, "w": context.workspace_id}).one_or_none()
        if row is None:
            return ReminderSettings()
        return ReminderSettings(reminder_hours=row.reminder_hours, updated_by=row.updated_by, updated_at=row.updated_at)

    def save(self, context: TenantContext, value: ReminderSettings) -> ReminderSettings:
        params = {"t": context.tenant_id, "w": context.workspace_id}
        if value.reminder_hours is None:
            self.session.execute(text("delete from approval_reminder_settings where tenant_id = :t and workspace_id = :w"), params)
            return value
        from .db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            """
            insert into approval_reminder_settings (tenant_id, workspace_id, reminder_hours, updated_by, updated_at)
            values (:t, :w, :h, :by, :at)
            on conflict (tenant_id, workspace_id) do update set reminder_hours = excluded.reminder_hours,
                updated_by = excluded.updated_by, updated_at = excluded.updated_at
            """), {**params, "h": value.reminder_hours, "by": value.updated_by, "at": value.updated_at})
        return value

    def claim(self, context: TenantContext, approval_id: str, now: datetime) -> bool:
        inserted = self.session.execute(text(
            """
            insert into approval_reminders (tenant_id, workspace_id, approval_id, recipients, sent_at)
            values (:t, :w, :a, 0, :now) on conflict do nothing returning approval_id
            """), {"t": context.tenant_id, "w": context.workspace_id, "a": approval_id, "now": now}).scalar_one_or_none()
        return inserted is not None

    def set_recipients(self, context: TenantContext, approval_id: str, count: int) -> None:
        self.session.execute(text(
            "update approval_reminders set recipients = :n where tenant_id = :t and workspace_id = :w and approval_id = :a"),
            {"t": context.tenant_id, "w": context.workspace_id, "a": approval_id, "n": count})

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)


@contextmanager
def open_reminder_store(context: TenantContext) -> Iterator[ReminderStore]:
    with open_scoped_store(context, memory_reminder_store, SqlReminderStore) as store:
        yield store


def _store_in(repository: Any) -> ReminderStore:
    if settings.repository_backend == "memory":
        return memory_reminder_store
    return SqlReminderStore(repository.session)


# --------------------------------------------------------------------------- the pass


def _waited(created_at: datetime, now: datetime) -> str:
    hours = max(1, int((now - created_at).total_seconds() // 3600))
    return f"{hours} hour" + ("" if hours == 1 else "s")


def remind_approval(context: TenantContext, approval_id: str, hours: int, now: datetime | None = None) -> int:
    """Send one approval's reminder to the workspace's owners; returns how many were told."""
    from .notifications import create_notification
    from .team_work import open_team_unit

    now = now or utc_now()
    with open_team_unit(context) as unit:
        approval = unit.repository.get_approval(approval_id, context)
        if approval is None or approval.status != ApprovalStatus.PENDING:
            return 0
        if approval.expires_at is not None and approval.expires_at <= now:
            return 0
        if approval.created_at > now - timedelta(hours=hours):
            return 0
        store = _store_in(unit.repository)
        if not store.claim(context, approval_id, now):
            return 0
        owners = sorted({m.user_id for m in unit.repository.list_memberships(context) if m.active and m.role.lower() == "owner"})
        title = f"Waiting for your decision: {approval.action}"
        expires = f" It expires at {approval.expires_at:%Y-%m-%d %H:%M} UTC." if approval.expires_at else ""
        body = (f"An approval for {approval.action} ({approval.risk_level.value} risk) has waited {_waited(approval.created_at, now)}."
                f"{expires} Open Approvals to approve or deny it.")
        from .events import CanonicalEventName, create_event

        for user_id in owners:
            notification = create_notification(unit.store, context, user_id=user_id, kind=NOTICE_KIND, title=title, body=body,
                                               work_item_id=None, in_app_only=True)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, notification.id,
                {"recipient": user_id, "kind": NOTICE_KIND, "work_item_id": None},
            ).event)
        store.set_recipients(context, approval_id, len(owners))
        unit.repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=SYSTEM_USER,
            action="approval.reminder_sent", target=f"approval:{approval_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=now, metadata={"recipients": len(owners), "after_hours": hours},
        ))
    return len(owners)


def _due_sql(batch: int, now: datetime) -> list[tuple[str, str, str, int]]:
    from .maintenance import default_session_factory, discover

    rows = discover(default_session_factory, text(
        """
        select a.tenant_id, a.workspace_id, a.id, s.reminder_hours
        from approvals a
        join approval_reminder_settings s on s.tenant_id = a.tenant_id and s.workspace_id = a.workspace_id
        where a.status = 'pending'
          and a.created_at <= :now - make_interval(hours => s.reminder_hours)
          and (a.expires_at is null or a.expires_at > :now)
          and not exists (
            select 1 from approval_reminders r
            where r.tenant_id = a.tenant_id and r.workspace_id = a.workspace_id and r.approval_id = a.id
          )
        order by a.created_at
        limit :batch
        """), {"now": now, "batch": batch})
    return [(row.tenant_id, row.workspace_id, row.id, int(row.reminder_hours)) for row in rows]


def _due_memory(now: datetime) -> list[tuple[str, str, str, int]]:
    from .dependencies import memory_repository

    due: list[tuple[str, str, str, int]] = []
    for tenant_id, workspace_id, hours in memory_reminder_store.scopes():
        ctx = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])
        for approval in memory_repository.list_approvals(ctx):
            if (approval.status == ApprovalStatus.PENDING and approval.created_at <= now - timedelta(hours=hours)
                    and (approval.expires_at is None or approval.expires_at > now)
                    and (tenant_id, workspace_id, approval.id) not in memory_reminder_store.sent):
                due.append((tenant_id, workspace_id, approval.id, hours))
    return due


def run_approval_reminder_pass(batch: int = 200, now: datetime | None = None) -> int:
    """One sweep over every tenant: remind each approval that has waited long enough."""
    now = now or utc_now()
    due = _due_sql(batch, now) if settings.repository_backend == "postgresql" else _due_memory(now)
    reminded = 0
    for tenant_id, workspace_id, approval_id, hours in due[:batch]:
        ctx = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])
        try:
            reminded += 1 if remind_approval(ctx, approval_id, hours, now) else 0
        except Exception:
            logger.exception("approval_reminder_failed", extra={"anum_approval_id": approval_id})
    return reminded


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1/approval-reminders", tags=["approvals"])


@router.get("/settings", response_model=ReminderSettings)
def get_reminder_settings(context: TenantContext = Depends(deps.tenant_context)) -> ReminderSettings:
    deps.require_permission(context, Permission.APPROVAL_READ)
    with open_reminder_store(context) as store:
        return store.get(context)


@router.put("/settings", response_model=ReminderSettings)
def set_reminder_settings(payload: ReminderSettingsUpdate, context: TenantContext = Depends(deps.tenant_context)) -> ReminderSettings:
    if not deps.has_permission(context, Permission.POLICY_MANAGE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SET_DENIED)
    now = utc_now()
    with deps._unit_of_work(context, require_membership=True) as repository:
        store = _store_in(repository)
        previous = store.get(context)
        saved = store.save(context, ReminderSettings(reminder_hours=payload.reminder_hours, updated_by=context.user_id, updated_at=now))
        store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="approval.reminder_settings_updated", target=f"workspace:{context.workspace_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=now,
            metadata={"reminder_hours": payload.reminder_hours, "previous": previous.reminder_hours},
        ))
    return saved if saved.reminder_hours is not None else ReminderSettings()
