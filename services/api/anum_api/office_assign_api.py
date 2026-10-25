"""Assign work to an agent without leaving the headquarters (docs/ANUM_MINE_ASSIGN_WORK.md).

``POST /api/v1/office/assignments`` is the office's one front door for "give this agent a job". It adds
nothing of its own to what an agent may do. It calls the same functions as ``POST /api/v1/tasks`` and
``POST /api/v1/tasks/{id}/run`` (``main.create_task`` and ``main.start_task_run``), so permission checks,
the plan's run capacity, the agent profile (paused agents are refused), the ToolPolicy, the agent's autonomy,
the monthly budget and approvals are exactly those of the Tasks page. A run that needs approval stops at
``waiting_approval`` and the answer says so; this route can never approve anything.

Idempotency: ``Idempotency-Key`` is required. The key is claimed in ``office_create_keys`` in the same
transaction that creates the task, so one key never makes two tasks. The same key with the same body
answers ``200`` with the existing task (``replayed: true``); the same key with a different body is ``409``.
The run is started after the task is committed. If it cannot start (no run permission, budget used up),
the task still exists and the answer carries ``run_error``; replaying the key tries the run again.

Every call is audited (``office.assignment.create``): ids, priority and source, never the instructions.
PostgreSQL only (503 otherwise), like the rest of the office.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context, units_of_work
from .office_scope import require_postgres
from .office_space_api import _store
from .schemas import TaskCreate, TaskPriority, TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/office/assignments", tags=["office assignments"])

AssignKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")]
SOURCES = ("agent_card", "desk", "room", "team_panel", "flow_dock", "executive_pa", "command", "other")
KEY_KIND = "task"
INSTRUCTIONS_MAX = 6000
EXPECTED_MAX = 1000
CONTEXT_MAX = 500


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    title: str = Field(min_length=1, max_length=160)
    instructions: str = Field(min_length=1, max_length=INSTRUCTIONS_MAX)
    expected_result: str | None = Field(default=None, max_length=EXPECTED_MAX)
    context_note: str | None = Field(default=None, max_length=CONTEXT_MAX)
    priority: TaskPriority = TaskPriority.MEDIUM
    due_at: datetime | None = None
    run: bool = True
    source: Literal["agent_card", "desk", "room", "team_panel", "flow_dock", "executive_pa", "command", "other"] = "other"

    @field_validator("title", "instructions")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Write something")
        return value.strip()

    @field_validator("expected_result", "context_note")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def compose_prompt(body: AssignBody) -> str:
    """The task prompt: the instructions, then the expected result and the context if given.

    The task model has no fields for them, so they travel inside the prompt where the agent reads them.
    """
    parts = [body.instructions]
    if body.expected_result:
        parts.append("Expected result:\n" + body.expected_result)
    if body.context_note:
        parts.append("Context:\n" + body.context_note)
    return "\n\n".join(parts)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def request_digest(body: AssignBody) -> str:
    """What the idempotency key protects: the task this body would create (not the source)."""
    due = _utc(body.due_at)
    canonical = json.dumps(
        {"agent": body.agent_id, "title": body.title, "prompt": compose_prompt(body), "priority": body.priority.value,
         "due": due.isoformat() if due else None, "run": body.run},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# Fixed texts by status: nothing from the exception is echoed to the client.
_RUN_REFUSALS = {
    402: "The monthly model budget is used up.",
    403: "You may not run this task.",
    404: "The task or the agent was not found.",
    409: "The task cannot be run from its current state, or the agent is paused.",
    429: "Too many runs right now. Try again shortly.",
}


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


async def _create_once(context: TenantContext, body: AssignBody, key: str, digest: str) -> tuple[Any, bool]:
    """Claim the key and create the task in one transaction. ``(task, created)``."""
    from . import main
    from .db.repository import SqlAlchemyRepository
    from .dependencies import _require_persisted_membership
    from .event_bus import EventCollectingRepository

    params = {"tenant": context.tenant_id, "workspace": context.workspace_id, "key": key, "digest": digest, "kind": KEY_KIND}
    with _store(context) as store:
        repository = EventCollectingRepository(SqlAlchemyRepository(store.session, created_by_user_id=context.user_id))
        _require_persisted_membership(repository, context)
        claimed = store.session.execute(text("""
            insert into office_create_keys (tenant_id, workspace_id, key, request_hash, resource_kind, resource_id)
            values (:tenant, :workspace, :key, :digest, :kind, 'pending')
            on conflict (tenant_id, workspace_id, key) do nothing
            returning key
        """), params).first()
        if claimed is None:
            row = store.session.execute(text("""
                select request_hash, resource_kind, resource_id from office_create_keys
                where tenant_id = :tenant and workspace_id = :workspace and key = :key
            """), params).mappings().one()
            if row["request_hash"] != digest or row["resource_kind"] != KEY_KIND:
                raise HTTPException(status.HTTP_409_CONFLICT, "This key was already used for a different assignment. Start a new one.")
            task = repository.get_task(row["resource_id"], context) if row["resource_id"] != "pending" else None
            if task is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "That assignment is no longer available. Start a new one.")
            return task, False
        task = await main.create_task(
            TaskCreate(title=body.title, prompt=compose_prompt(body), priority=body.priority,
                       due_at=_utc(body.due_at), agent_id=body.agent_id),
            context, repository,
        )
        store.session.execute(text("""
            update office_create_keys set resource_id = :task
            where tenant_id = :tenant and workspace_id = :workspace and key = :key
        """), {**params, "task": task.id})
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action="office.assignment.create", target=task.id, outcome="success",
            correlation_id=task.id, created_at=utc_now(),
            metadata={"resource": "office_assignment", "agent_id": body.agent_id, "task_id": task.id,
                      "priority": body.priority.value, "has_due": body.due_at is not None, "run_requested": body.run,
                      "source": body.source},
        ))
        store.pending_events.extend(repository.recorded_events)  # type: ignore[attr-defined]
        return task, True


@router.post("")
async def assign_work(
    body: AssignBody,
    response: Response,
    idempotency_key: AssignKey,
    context: TenantContext = Depends(tenant_context),
    units: Any = Depends(units_of_work),
) -> dict:
    """Create a task for an agent (and run it unless ``run`` is false) by the Tasks page's own code."""
    from . import main

    require_postgres()
    require_permission(context, Permission.TASK_CREATE)
    if body.run:
        require_permission(context, Permission.TASK_RUN)
    # The database work runs in its own thread and loop, never on the server's loop (docs/realtime.md#event-loop).
    task, created = await asyncio.to_thread(
        lambda: asyncio.run(_create_once(context, body, idempotency_key, request_digest(body))))
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    out: dict[str, Any] = {"task": _dump(task), "replayed": not created, "run": None, "approval": None, "run_error": None}
    if body.run and getattr(task, "status", None) is not None and task.status.value == "created":
        try:
            result = await main.start_task_run(task.id, context, units)
            out.update(task=_dump(result.task), run=_dump(result.run), approval=_dump(result.approval))
        except HTTPException as exc:
            out["run_error"] = {"status_code": exc.status_code, "detail": _RUN_REFUSALS.get(exc.status_code, "The run could not be started. Open the task and run it from Tasks.")}
    return out


__all__ = ["router", "compose_prompt", "request_digest", "AssignBody"]
