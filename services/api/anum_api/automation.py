"""Automation workflows, schedules and runs.

Two engines implement one contract (``AutomationEngine``):

- ``LocalAutomationEngine`` keeps everything in a local SQLite file
  (``ANUM_AUTOMATION_DATABASE_PATH``). It is the engine for ``ANUM_REPOSITORY_BACKEND=memory``
  (local development and tests) and serves one process.
- ``PostgresAutomationEngine`` (``anum_api.db.automation_repository``) keeps them in the
  RLS-protected ``automation_*`` tables (migration 0009) when
  ``ANUM_REPOSITORY_BACKEND=postgresql``, so any number of API replicas share them.

``AutomationScheduler`` fires due schedules. With PostgreSQL it is safe on every replica:
due schedules are claimed with ``FOR UPDATE SKIP LOCKED`` and advanced in the same
transaction that records the run, and each fire has a unique idempotency key
(``schedule:<id>:<fire time>``), so a fire time never produces two runs.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from . import cron
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import ScopeNotProvisionedError
from .settings import settings

logger = logging.getLogger(__name__)

SCHEDULER_ACTOR = "system:automation-scheduler"


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStep(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=120)
    input: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=100)

    @field_validator("steps")
    @classmethod
    def unique_step_ids(cls, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        if len({step.id for step in steps}) != len(steps):
            raise ValueError("Workflow step IDs must be unique")
        return steps


class WorkflowDefinition(WorkflowCreate):
    id: str
    tenant_id: str
    workspace_id: str
    status: WorkflowStatus
    version: int
    created_at: datetime
    updated_at: datetime


def _valid_cron(value: str) -> str:
    try:
        cron.CronExpression.parse(value)
    except cron.CronError as exc:
        raise ValueError(str(exc)) from None
    return value


def _valid_timezone(value: str) -> str:
    try:
        cron.zone(value)
    except cron.CronError as exc:
        raise ValueError(str(exc)) from None
    return value


class ScheduleCreate(BaseModel):
    workflow_id: str
    name: str = Field(min_length=1, max_length=160)
    cron: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        return _valid_cron(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _valid_timezone(value)

    @model_validator(mode="after")
    def fires(self) -> ScheduleCreate:
        try:
            cron.validate(self.cron, self.timezone)
        except cron.CronError as exc:
            raise ValueError(str(exc)) from None
        return self


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cron: str | None = Field(default=None, min_length=9, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str | None) -> str | None:
        return None if value is None else _valid_cron(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        return None if value is None else _valid_timezone(value)


class AutomationSchedule(BaseModel):
    workflow_id: str
    name: str
    cron: str
    timezone: str = "UTC"
    enabled: bool = True
    id: str
    tenant_id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime
    # Next fire time in UTC; null while the schedule is disabled.
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_by: str | None = None


class RunStepState(BaseModel):
    id: str
    name: str
    action: str
    status: RunStatus
    attempt: int = 0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WorkflowRun(BaseModel):
    id: str
    workflow_id: str
    tenant_id: str
    workspace_id: str
    status: RunStatus
    idempotency_key: str | None = None
    retry_of: str | None = None
    current_step: int = 0
    steps: list[RunStepState]
    created_at: datetime
    updated_at: datetime
    # Set when a schedule fired the run.
    schedule_id: str | None = None
    created_by: str | None = None


class AutomationEngine(Protocol):
    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition: ...
    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]: ...
    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule: ...
    def list_schedules(self, context: TenantContext) -> list[AutomationSchedule]: ...
    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule: ...
    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule: ...
    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None: ...
    def start(self, context: TenantContext, workflow_id: str, idempotency_key: str | None = None, retry_of: str | None = None) -> WorkflowRun: ...
    def list_runs(self, context: TenantContext) -> list[WorkflowRun]: ...
    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def run_due_schedules(self, *, limit: int = 100) -> list[WorkflowRun]: ...


# Engine-independent run logic --------------------------------------------------------


def next_fire(cron_expression: str, timezone_name: str, after: datetime) -> datetime:
    return cron.CronExpression.parse(cron_expression).next_after(after, timezone_name)


def schedule_fire_key(schedule_id: str, fire_at: datetime) -> str:
    """Idempotency key of one scheduled fire: a fire time can produce one run only."""
    return f"schedule:{schedule_id}:{fire_at.isoformat()}"


def new_run(
    context: TenantContext,
    workflow: WorkflowDefinition,
    *,
    idempotency_key: str | None = None,
    retry_of: str | None = None,
    schedule_id: str | None = None,
) -> WorkflowRun:
    now = utc_now()
    return WorkflowRun(
        id=new_id("automation_run"),
        workflow_id=workflow.id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        status=RunStatus.QUEUED,
        idempotency_key=idempotency_key,
        retry_of=retry_of,
        schedule_id=schedule_id,
        created_by=context.user_id,
        steps=[
            RunStepState(id=step.id, name=step.name, action=step.action, status=RunStatus.QUEUED)
            for step in workflow.steps
        ],
        created_at=now,
        updated_at=now,
    )


def execute_run(run: WorkflowRun, workflow: WorkflowDefinition) -> WorkflowRun:
    run.status = RunStatus.RUNNING
    while run.current_step < len(run.steps):
        state = run.steps[run.current_step]
        definition = workflow.steps[run.current_step]
        state.attempt += 1
        if definition.action == "pause":
            state.status = RunStatus.PAUSED
            run.status = RunStatus.PAUSED
            break
        if definition.action == "fail":
            state.status = RunStatus.FAILED
            state.error = str(definition.input.get("message", "Step failed"))
            run.status = RunStatus.FAILED
            break
        state.status = RunStatus.COMPLETED
        state.output = {"accepted": True, "action": definition.action}
        run.current_step += 1
    if run.current_step == len(run.steps):
        run.status = RunStatus.COMPLETED
    run.updated_at = utc_now()
    return run


def cancel_state(run: WorkflowRun) -> WorkflowRun:
    if run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
        raise ValueError("Run cannot be cancelled from current state")
    run.status = RunStatus.CANCELLED
    run.updated_at = utc_now()
    return run


def resume_state(run: WorkflowRun, workflow: WorkflowDefinition) -> WorkflowRun:
    if run.status != RunStatus.PAUSED:
        raise ValueError("Only paused runs can be resumed")
    run.steps[run.current_step].status = RunStatus.COMPLETED
    run.steps[run.current_step].output = {"resumed": True}
    run.current_step += 1
    return execute_run(run, workflow)


def scheduler_context(tenant_id: str, workspace_id: str, actor: str | None) -> TenantContext:
    """The explicit actor and tenant a scheduled run executes under."""
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor or SCHEDULER_ACTOR,
        roles=[],
    )


# Local SQLite engine -------------------------------------------------------------------

# Every statement is a fixed string per table: nothing is interpolated into SQL.
_SAVE = {
    "automation_workflows": "insert or replace into automation_workflows (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
    "automation_schedules": "insert or replace into automation_schedules (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
}
_LIST = {
    "automation_workflows": "select body from automation_workflows where tenant_id = ? and workspace_id = ? order by created_at desc",
    "automation_schedules": "select body from automation_schedules where tenant_id = ? and workspace_id = ? order by created_at desc",
    "automation_runs": "select body from automation_runs where tenant_id = ? and workspace_id = ? order by created_at desc",
}
_SAVE_RUN = (
    "insert or replace into automation_runs (id, tenant_id, workspace_id, workflow_id, idempotency_key, body, created_at) "
    "values (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_RUN_ONCE = (
    "insert or ignore into automation_runs (id, tenant_id, workspace_id, workflow_id, idempotency_key, body, created_at) "
    "values (?, ?, ?, ?, ?, ?, ?)"
)
_ALL_SCHEDULES = "select tenant_id, workspace_id, body from automation_schedules"


class LocalAutomationEngine:
    """SQLite orchestration backend implementing the Temporal-facing contract."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.executescript("""
                create table if not exists automation_workflows (
                    id text primary key, tenant_id text not null, workspace_id text not null,
                    body text not null, created_at text not null
                );
                create table if not exists automation_schedules (
                    id text primary key, tenant_id text not null, workspace_id text not null,
                    body text not null, created_at text not null
                );
                create table if not exists automation_runs (
                    id text primary key, tenant_id text not null, workspace_id text not null,
                    workflow_id text not null, idempotency_key text, body text not null, created_at text not null
                );
                create unique index if not exists automation_run_idempotency
                on automation_runs(tenant_id, workspace_id, idempotency_key)
                where idempotency_key is not null;
            """)
            connection.commit()
            self._initialized = True
        return connection

    @staticmethod
    def _scope(context: TenantContext) -> tuple[str, str]:
        return context.tenant_id, context.workspace_id

    def _save(self, table: str, value: BaseModel, context: TenantContext) -> None:
        statement = _SAVE[table]
        with self._connect() as connection:
            connection.execute(
                statement,
                (value.id, *self._scope(context), value.model_dump_json(), value.created_at.isoformat()),  # type: ignore[attr-defined]
            )

    @staticmethod
    def _run_values(run: WorkflowRun) -> tuple[Any, ...]:
        return (
            run.id,
            run.tenant_id,
            run.workspace_id,
            run.workflow_id,
            run.idempotency_key,
            run.model_dump_json(),
            run.created_at.isoformat(),
        )

    def _save_run(self, run: WorkflowRun) -> None:
        with self._connect() as connection:
            connection.execute(_SAVE_RUN, self._run_values(run))

    def _list(self, table: str, model: type[BaseModel], context: TenantContext) -> list[Any]:
        with self._connect() as connection:
            rows = connection.execute(_LIST[table], self._scope(context)).fetchall()
        return [model.model_validate_json(row["body"]) for row in rows]

    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition:
        now = utc_now()
        workflow = WorkflowDefinition(id=new_id("workflow"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, status=WorkflowStatus.ACTIVE, version=1, created_at=now, updated_at=now, **payload.model_dump())
        self._save("automation_workflows", workflow, context)
        return workflow

    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]:
        return self._list("automation_workflows", WorkflowDefinition, context)

    def _workflow(self, context: TenantContext, workflow_id: str) -> WorkflowDefinition:
        workflow = next((item for item in self.list_workflows(context) if item.id == workflow_id), None)
        if workflow is None:
            raise KeyError(workflow_id)
        return workflow

    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule:
        self._workflow(context, payload.workflow_id)
        now = utc_now()
        schedule = AutomationSchedule(
            id=new_id("schedule"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            created_at=now,
            updated_at=now,
            created_by=context.user_id,
            next_run_at=next_fire(payload.cron, payload.timezone, now) if payload.enabled else None,
            **payload.model_dump(),
        )
        self._save("automation_schedules", schedule, context)
        return schedule

    def list_schedules(self, context: TenantContext) -> list[AutomationSchedule]:
        return self._list("automation_schedules", AutomationSchedule, context)

    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        schedule = next((item for item in self.list_schedules(context) if item.id == schedule_id), None)
        if schedule is None:
            raise KeyError(schedule_id)
        return schedule

    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule:
        with self._lock:
            schedule = self.get_schedule(context, schedule_id)
            updates = payload.model_dump(exclude_none=True)
            now = utc_now()
            updated = schedule.model_copy(update={**updates, "updated_at": now})
            cron.validate(updated.cron, updated.timezone)
            if updated.enabled and (not schedule.enabled or {"cron", "timezone"} & updates.keys()):
                updated.next_run_at = next_fire(updated.cron, updated.timezone, now)
            elif not updated.enabled:
                updated.next_run_at = None
            self._save("automation_schedules", updated, context)
            return updated

    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None:
        self.get_schedule(context, schedule_id)
        with self._connect() as connection:
            connection.execute("delete from automation_schedules where id = ? and tenant_id = ? and workspace_id = ?", (schedule_id, *self._scope(context)))

    def _run(self, context: TenantContext, run_id: str) -> WorkflowRun:
        run = next((item for item in self.list_runs(context) if item.id == run_id), None)
        if run is None:
            raise KeyError(run_id)
        return run

    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun:
        return self._run(context, run_id)

    def start(self, context: TenantContext, workflow_id: str, idempotency_key: str | None = None, retry_of: str | None = None) -> WorkflowRun:
        with self._lock:
            if idempotency_key:
                existing = next((run for run in self.list_runs(context) if run.idempotency_key == idempotency_key), None)
                if existing:
                    return existing
            workflow = self._workflow(context, workflow_id)
            run = execute_run(new_run(context, workflow, idempotency_key=idempotency_key, retry_of=retry_of), workflow)
            self._save_run(run)
            return run

    def list_runs(self, context: TenantContext) -> list[WorkflowRun]:
        return self._list("automation_runs", WorkflowRun, context)

    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._lock:
            run = cancel_state(self._run(context, run_id))
            self._save_run(run)
            return run

    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._run(context, run_id)
            run = resume_state(run, self._workflow(context, run.workflow_id))
            self._save_run(run)
            return run

    def run_due_schedules(self, *, limit: int = 100, now: datetime | None = None) -> list[WorkflowRun]:
        """Fire every due schedule once; a missed fire runs once, it does not catch up.

        ``BEGIN IMMEDIATE`` holds SQLite's write lock for the whole pass, so two local
        processes sharing the file cannot fire the same schedule twice either.
        """
        now = now or utc_now()
        runs: list[WorkflowRun] = []
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("begin immediate")
                rows = connection.execute(_ALL_SCHEDULES).fetchall()
                due = [
                    schedule
                    for schedule in (AutomationSchedule.model_validate_json(row["body"]) for row in rows)
                    if schedule.enabled and schedule.next_run_at is not None and schedule.next_run_at <= now
                ]
                due.sort(key=lambda item: item.next_run_at or now)
                for schedule in due[:limit]:
                    fire_at = schedule.next_run_at or now
                    context = scheduler_context(schedule.tenant_id, schedule.workspace_id, schedule.created_by)
                    workflow_rows = connection.execute(_LIST["automation_workflows"], self._scope(context)).fetchall()
                    workflow = next(
                        (
                            item
                            for item in (WorkflowDefinition.model_validate_json(row["body"]) for row in workflow_rows)
                            if item.id == schedule.workflow_id
                        ),
                        None,
                    )
                    if workflow is not None and workflow.status == WorkflowStatus.ACTIVE:
                        run = new_run(
                            context,
                            workflow,
                            idempotency_key=schedule_fire_key(schedule.id, fire_at),
                            schedule_id=schedule.id,
                        )
                        execute_run(run, workflow)
                        inserted = connection.execute(_INSERT_RUN_ONCE, self._run_values(run)).rowcount
                        if inserted:
                            runs.append(run)
                    schedule.last_run_at = now
                    schedule.next_run_at = next_fire(schedule.cron, schedule.timezone, max(now, fire_at))
                    schedule.updated_at = now
                    connection.execute(
                        _SAVE["automation_schedules"],
                        (schedule.id, schedule.tenant_id, schedule.workspace_id, schedule.model_dump_json(), schedule.created_at.isoformat()),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        return runs


# Engine selection -------------------------------------------------------------------

engine: AutomationEngine = LocalAutomationEngine(settings.automation_database_path)
_postgres_engine: AutomationEngine | None = None


def current_engine() -> AutomationEngine:
    """PostgreSQL engine with ``ANUM_REPOSITORY_BACKEND=postgresql``, else the local one."""
    global _postgres_engine
    if settings.repository_backend == "postgresql":
        if _postgres_engine is None:
            from .db.automation_repository import PostgresAutomationEngine
            from .maintenance import default_session_factory

            _postgres_engine = PostgresAutomationEngine(default_session_factory)
        return _postgres_engine
    return engine


# Scheduler --------------------------------------------------------------------------


class AutomationScheduler:
    """Background loop that fires due schedules (``ANUM_AUTOMATION_SCHEDULER_ENABLED``).

    Each pass runs in a worker thread. ``stop`` lets a pass in progress finish (its
    transactions commit or roll back and its sessions close) before returning, and only
    cancels the waiting loop, never a thread in the middle of a transaction.
    """

    def __init__(
        self,
        engine_provider: Callable[[], AutomationEngine] = current_engine,
        *,
        poll_interval: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        self.engine_provider = engine_provider
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.fired = 0
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._pass: asyncio.Future[Any] | None = None

    def run_once(self) -> list[WorkflowRun]:
        runs = self.engine_provider().run_due_schedules(limit=self.batch_size)
        self.fired += len(runs)
        return runs

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="anum-automation-scheduler")

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Automation scheduler stopped with an error")
        # A pass thread that outlived the timeout still finishes its own transaction.
        if self._pass is not None and not self._pass.done():
            await asyncio.wait({self._pass})
        self._pass = None
        self._stop = None

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Automation scheduler started without a stop event")
        while not stop.is_set():
            self._pass = asyncio.ensure_future(asyncio.to_thread(self.run_once))
            try:
                await asyncio.shield(self._pass)
            except asyncio.CancelledError:
                await asyncio.wait({self._pass})
                raise
            except Exception:
                logger.exception("Automation scheduler pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_automation_scheduler(config: Any) -> AutomationScheduler | None:
    if not config.automation_scheduler_enabled:
        return None
    return AutomationScheduler(
        poll_interval=config.automation_scheduler_poll_seconds,
        batch_size=config.automation_scheduler_batch_size,
    )


# API ----------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


def _not_found_or_conflict(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeNotProvisionedError):
        return HTTPException(status_code=409, detail="Complete onboarding for this workspace first")
    return HTTPException(status_code=404 if isinstance(exc, KeyError) else 409, detail=str(exc))


_HANDLED = (KeyError, ValueError, ScopeNotProvisionedError)


@router.post("/workflows", response_model=WorkflowDefinition, status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreate, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().create_workflow(context, payload)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.get("/workflows", response_model=list[WorkflowDefinition])
def list_workflows(context: TenantContext = Depends(tenant_context)) -> list[WorkflowDefinition]:
    require_permission(context, Permission.AUTOMATION_READ)
    return current_engine().list_workflows(context)


@router.post("/schedules", response_model=AutomationSchedule, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().create_schedule(context, payload)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.get("/schedules", response_model=list[AutomationSchedule])
def list_schedules(context: TenantContext = Depends(tenant_context)) -> list[AutomationSchedule]:
    require_permission(context, Permission.AUTOMATION_READ)
    return current_engine().list_schedules(context)


@router.get("/schedules/{schedule_id}", response_model=AutomationSchedule)
def get_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_READ)
    try:
        return current_engine().get_schedule(context, schedule_id)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.put("/schedules/{schedule_id}", response_model=AutomationSchedule)
def update_schedule(schedule_id: str, payload: ScheduleUpdate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().update_schedule(context, schedule_id, payload)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/schedules/{schedule_id}/{action}", response_model=AutomationSchedule)
def toggle_schedule(schedule_id: str, action: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="Schedule action not found")
    try:
        return current_engine().update_schedule(context, schedule_id, ScheduleUpdate(enabled=action == "enable"))
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        current_engine().delete_schedule(context, schedule_id)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workflows/{workflow_id}/runs", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def start_run(workflow_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().start(context, workflow_id, idempotency_key)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.get("/runs", response_model=list[WorkflowRun])
def list_runs(context: TenantContext = Depends(tenant_context)) -> list[WorkflowRun]:
    require_permission(context, Permission.AUTOMATION_READ)
    return current_engine().list_runs(context)


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRun)
def cancel_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().cancel(context, run_id)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/runs/{run_id}/resume", response_model=WorkflowRun)
def resume_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return current_engine().resume(context, run_id)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/runs/{run_id}/retry", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def retry_run(run_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        selected = current_engine()
        prior = selected.get_run(context, run_id)
        if prior.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError("Only failed or cancelled runs can be retried")
        return selected.start(context, prior.workflow_id, idempotency_key, retry_of=prior.id)
    except _HANDLED as exc:
        raise _not_found_or_conflict(exc) from exc
