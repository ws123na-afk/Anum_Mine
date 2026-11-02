"""Per-agent loop and retry limits (docs/agent-controls.md#loop-and-retry-limits).

A workspace owner may give each agent profile two limits, next to its monthly caps:

* ``max_runs_per_task`` (1 to 20): the most runs one task as this agent may have, the first
  run included. It is the agent's loop limit: once a task has that many runs, retrying it is
  refused with ``409`` and a sentence saying who can raise it.
* ``max_step_attempts`` (1 to 10): the most attempts an automation step that runs as this
  agent may make, whatever the step itself allows. The lower of the two applies.

Without a row the platform's own limits apply (a task may be retried by hand; a step uses its
own ``max_attempts``). Limits only ever tighten what the agent may do. Changes are audited as
``agent_controls.loop_limit.update``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import dependencies as deps
from .audit import AuditRecord
from .authorization import Permission
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

MAX_RUNS_PER_TASK = 20
MAX_STEP_ATTEMPTS = 10
SET_DENIED = "Only a workspace owner can change an agent's loop and retry limits."
READ_DENIED = "Only a workspace owner or the organization administrator can see agent loop and retry limits."


class LoopLimit(BaseModel):
    agent_id: str
    max_runs_per_task: int | None = None
    max_step_attempts: int | None = None
    updated_by: str
    updated_at: datetime


class LoopLimitWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_runs_per_task: int | None = Field(default=None, ge=1, le=MAX_RUNS_PER_TASK)
    max_step_attempts: int | None = Field(default=None, ge=1, le=MAX_STEP_ATTEMPTS)


class LoopLimitView(BaseModel):
    agent_id: str
    name: str
    status: str
    max_runs_per_task: int | None = None
    max_step_attempts: int | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class LoopLimitPage(BaseModel):
    can_manage: bool
    total: int
    items: list[LoopLimitView]


class LoopLimitStore(Protocol):
    def list(self, context: TenantContext) -> list[LoopLimit]: ...
    def get(self, context: TenantContext, agent_id: str) -> LoopLimit | None: ...
    def set(self, context: TenantContext, agent_id: str, value: LoopLimitWrite, now: datetime) -> LoopLimit | None: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryLoopLimitStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], LoopLimit] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def list(self, context: TenantContext) -> list[LoopLimit]:
        with self._lock:
            return [row.model_copy() for (t, w, _), row in self._rows.items() if (t, w) == (context.tenant_id, context.workspace_id)]

    def get(self, context: TenantContext, agent_id: str) -> LoopLimit | None:
        with self._lock:
            row = self._rows.get((context.tenant_id, context.workspace_id, agent_id))
            return row.model_copy() if row else None

    def set(self, context: TenantContext, agent_id: str, value: LoopLimitWrite, now: datetime) -> LoopLimit | None:
        key = (context.tenant_id, context.workspace_id, agent_id)
        with self._lock:
            if value.max_runs_per_task is None and value.max_step_attempts is None:
                self._rows.pop(key, None)
                return None
            row = LoopLimit(agent_id=agent_id, max_runs_per_task=value.max_runs_per_task, max_step_attempts=value.max_step_attempts,
                            updated_by=context.user_id, updated_at=now)
            self._rows[key] = row
            return row.model_copy()

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from .dependencies import memory_repository

        return memory_repository.record_audit(record)


memory_loop_limit_store = InMemoryLoopLimitStore()


class SqlLoopLimitStore:
    """``agent_loop_limits`` (migration 0128), forced RLS on the tenant and workspace."""

    def __init__(self, session: Any) -> None:
        from .db.repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    @staticmethod
    def _row(row: Any) -> LoopLimit:
        return LoopLimit(agent_id=row.agent_id, max_runs_per_task=row.max_runs_per_task, max_step_attempts=row.max_step_attempts,
                         updated_by=row.updated_by, updated_at=row.updated_at)

    def list(self, context: TenantContext) -> list[LoopLimit]:
        rows = self.session.execute(text(
            "select agent_id, max_runs_per_task, max_step_attempts, updated_by, updated_at from agent_loop_limits "
            "where tenant_id = :t and workspace_id = :w"), {"t": context.tenant_id, "w": context.workspace_id}).all()
        return [self._row(r) for r in rows]

    def get(self, context: TenantContext, agent_id: str) -> LoopLimit | None:
        row = self.session.execute(text(
            "select agent_id, max_runs_per_task, max_step_attempts, updated_by, updated_at from agent_loop_limits "
            "where tenant_id = :t and workspace_id = :w and agent_id = :a"),
            {"t": context.tenant_id, "w": context.workspace_id, "a": agent_id}).one_or_none()
        return self._row(row) if row is not None else None

    def set(self, context: TenantContext, agent_id: str, value: LoopLimitWrite, now: datetime) -> LoopLimit | None:
        params = {"t": context.tenant_id, "w": context.workspace_id, "a": agent_id}
        if value.max_runs_per_task is None and value.max_step_attempts is None:
            self.session.execute(text("delete from agent_loop_limits where tenant_id = :t and workspace_id = :w and agent_id = :a"), params)
            return None
        from .db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            """
            insert into agent_loop_limits (tenant_id, workspace_id, agent_id, max_runs_per_task, max_step_attempts, updated_by, updated_at)
            values (:t, :w, :a, :runs, :attempts, :by, :at)
            on conflict (tenant_id, workspace_id, agent_id) do update set max_runs_per_task = excluded.max_runs_per_task,
                max_step_attempts = excluded.max_step_attempts, updated_by = excluded.updated_by, updated_at = excluded.updated_at
            """), {**params, "runs": value.max_runs_per_task, "attempts": value.max_step_attempts, "by": context.user_id, "at": now})
        return LoopLimit(agent_id=agent_id, max_runs_per_task=value.max_runs_per_task, max_step_attempts=value.max_step_attempts,
                         updated_by=context.user_id, updated_at=now)

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)


@contextmanager
def open_loop_limit_store(context: TenantContext) -> Iterator[LoopLimitStore]:
    with open_scoped_store(context, memory_loop_limit_store, SqlLoopLimitStore) as store:
        yield store


def loop_limit(context: TenantContext, agent_id: str) -> LoopLimit | None:
    with open_loop_limit_store(context) as store:
        return store.get(context, agent_id)


# --------------------------------------------------------------------------- enforcement


def loop_limit_refusal(name: str, limit: int) -> str:
    return (f"{name} has reached its loop limit: a task as this agent may have at most {limit} runs. "
            "A workspace owner can raise it under Agent controls, or start a new task.")


def check_retry_allowed(context: TenantContext, repository: Any, task: Any) -> None:
    """Refuse (409) another run of ``task`` when its agent's loop limit is reached."""
    if not getattr(task, "agent_id", None):
        return
    limit = loop_limit(context, task.agent_id)
    if limit is None or limit.max_runs_per_task is None:
        return
    runs = repository.list_runs_for_task(task.id, context, limit=limit.max_runs_per_task + 1)
    if len(runs) >= limit.max_runs_per_task:
        from .agent_profiles import load_agent

        profile = load_agent(context, task.agent_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=loop_limit_refusal(profile.name if profile else "This agent", limit.max_runs_per_task))


def automation_agent(context: TenantContext, agent_id: str) -> tuple[str | None, int | None]:
    """For an automation step that runs as ``agent_id``: why it cannot run now (or None) and the
    agent's step attempt limit (or None)."""
    from .agent_profiles import AgentStatus, load_agent

    profile = load_agent(context, agent_id)
    if profile is None:
        return ("The agent this step runs as was removed, or the automation's owner can no longer see it. "
                "Choose another agent in the step, or none."), None
    if profile.status != AgentStatus.ACTIVE:
        return f"{profile.name} is paused. Resume it in Agents, then retry the run.", None
    limit = loop_limit(context, agent_id)
    return None, limit.max_step_attempts if limit else None


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1/agent-controls/loop-limits", tags=["agent-controls"])


def _audit(store: LoopLimitStore, context: TenantContext, agent_id: str, previous: LoopLimit | None, saved: LoopLimit | None) -> None:
    def describe(value: LoopLimit | None) -> str:
        if value is None:
            return "none"
        return f"runs_per_task={value.max_runs_per_task or 'none'} step_attempts={value.max_step_attempts or 'none'}"

    store.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action="agent_controls.loop_limit.update", target=f"agent:{agent_id}", outcome="success",
        correlation_id=new_id("correlation"), created_at=utc_now(),
        metadata={"previous_limits": describe(previous), "limits": describe(saved)},
    ))


def _view(agent_id: str, name: str, state: str, row: LoopLimit | None) -> LoopLimitView:
    return LoopLimitView(agent_id=agent_id, name=name, status=state,
                         max_runs_per_task=row.max_runs_per_task if row else None,
                         max_step_attempts=row.max_step_attempts if row else None,
                         updated_by=row.updated_by if row else None, updated_at=row.updated_at if row else None)


@router.get("", response_model=LoopLimitPage)
def list_loop_limits(
    context: TenantContext = Depends(deps.tenant_context),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> LoopLimitPage:
    """Every agent the caller can see with its loop and retry limits."""
    deps.require_owner_or_org_admin(context, Permission.ORGANIZATION_MANAGE, READ_DENIED)
    from .agent_profiles import open_agent_store

    with open_agent_store(context) as agents:
        profiles = agents.list(context)
    with open_loop_limit_store(context) as store:
        rows = {row.agent_id: row for row in store.list(context)}
    needle = (q or "").strip().lower()
    found = [p for p in sorted(profiles, key=lambda p: (p.name.lower(), p.id)) if not needle or needle in p.name.lower()]
    return LoopLimitPage(
        can_manage=deps.has_permission(context, Permission.ORGANIZATION_MANAGE), total=len(found),
        items=[_view(p.id, p.name, p.status.value, rows.get(p.id)) for p in found[offset: offset + limit]],
    )


@router.put("/{agent_id}", response_model=LoopLimitView)
def set_loop_limit(agent_id: str, payload: LoopLimitWrite, context: TenantContext = Depends(deps.tenant_context)) -> LoopLimitView:
    """Set (or clear with nulls) one agent's loop and retry limits (workspace owners)."""
    if not deps.has_permission(context, Permission.ORGANIZATION_MANAGE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SET_DENIED)
    from .agent_profiles import open_agent_store

    with open_agent_store(context) as agents:
        profile = agents.get(context, agent_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found in this workspace")
    with deps._unit_of_work(context, require_membership=True) as repository:
        store: LoopLimitStore = (memory_loop_limit_store if _memory() else SqlLoopLimitStore(repository.session))
        previous = store.get(context, agent_id)
        saved = store.set(context, agent_id, payload, utc_now())
        _audit(store, context, agent_id, previous, saved)
    return _view(agent_id, profile.name, profile.status.value, saved)


def _memory() -> bool:
    from .settings import settings

    return settings.repository_backend == "memory"
