from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .settings import settings


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


class ScheduleCreate(BaseModel):
    workflow_id: str
    name: str = Field(min_length=1, max_length=160)
    cron: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        if len(value.split()) != 5:
            raise ValueError("Cron expressions must contain five fields")
        return value


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cron: str | None = Field(default=None, min_length=9, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str | None) -> str | None:
        if value is not None and len(value.split()) != 5:
            raise ValueError("Cron expressions must contain five fields")
        return value


class AutomationSchedule(ScheduleCreate):
    id: str
    tenant_id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime


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

    def _save(self, table: str, value: BaseModel, context: TenantContext, **columns: str | None) -> None:
        body = value.model_dump_json()
        with self._connect() as connection:
            if table == "automation_runs":
                connection.execute(
                    "insert or replace into automation_runs (id, tenant_id, workspace_id, workflow_id, idempotency_key, body, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                    (value.id, *self._scope(context), columns["workflow_id"], columns.get("idempotency_key"), body, value.created_at.isoformat()),
                )
            else:
                connection.execute(
                    f"insert or replace into {table} (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
                    (value.id, *self._scope(context), body, value.created_at.isoformat()),
                )

    def _list(self, table: str, model: type[BaseModel], context: TenantContext) -> list[Any]:
        with self._connect() as connection:
            rows = connection.execute(
                f"select body from {table} where tenant_id = ? and workspace_id = ? order by created_at desc",
                self._scope(context),
            ).fetchall()
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
        schedule = AutomationSchedule(id=new_id("schedule"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now, **payload.model_dump())
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
        schedule = self.get_schedule(context, schedule_id)
        updates = payload.model_dump(exclude_none=True)
        updated = schedule.model_copy(update={**updates, "updated_at": utc_now()})
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

    def _execute(self, run: WorkflowRun, workflow: WorkflowDefinition) -> WorkflowRun:
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

    def start(self, context: TenantContext, workflow_id: str, idempotency_key: str | None = None, retry_of: str | None = None) -> WorkflowRun:
        with self._lock:
            if idempotency_key:
                existing = next((run for run in self.list_runs(context) if run.idempotency_key == idempotency_key), None)
                if existing:
                    return existing
            workflow = self._workflow(context, workflow_id)
            now = utc_now()
            run = WorkflowRun(id=new_id("automation_run"), workflow_id=workflow.id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, status=RunStatus.QUEUED, idempotency_key=idempotency_key, retry_of=retry_of, steps=[RunStepState(id=step.id, name=step.name, action=step.action, status=RunStatus.QUEUED) for step in workflow.steps], created_at=now, updated_at=now)
            self._execute(run, workflow)
            self._save("automation_runs", run, context, workflow_id=workflow.id, idempotency_key=idempotency_key)
            return run

    def list_runs(self, context: TenantContext) -> list[WorkflowRun]:
        return self._list("automation_runs", WorkflowRun, context)

    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._run(context, run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
                raise ValueError("Run cannot be cancelled from current state")
            run.status = RunStatus.CANCELLED
            run.updated_at = utc_now()
            self._save("automation_runs", run, context, workflow_id=run.workflow_id, idempotency_key=run.idempotency_key)
            return run

    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._run(context, run_id)
            if run.status != RunStatus.PAUSED:
                raise ValueError("Only paused runs can be resumed")
            run.steps[run.current_step].status = RunStatus.COMPLETED
            run.steps[run.current_step].output = {"resumed": True}
            run.current_step += 1
            workflow = self._workflow(context, run.workflow_id)
            self._execute(run, workflow)
            self._save("automation_runs", run, context, workflow_id=run.workflow_id, idempotency_key=run.idempotency_key)
            return run


engine: AutomationEngine = LocalAutomationEngine(settings.automation_database_path)
router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


def _not_found_or_conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, KeyError) else 409, detail=str(exc))


@router.post("/workflows", response_model=WorkflowDefinition, status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreate, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    return engine.create_workflow(context, payload)


@router.get("/workflows", response_model=list[WorkflowDefinition])
def list_workflows(context: TenantContext = Depends(tenant_context)) -> list[WorkflowDefinition]:
    require_permission(context, Permission.AUTOMATION_READ)
    return engine.list_workflows(context)


@router.post("/schedules", response_model=AutomationSchedule, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return engine.create_schedule(context, payload)
    except KeyError as exc:
        raise _not_found_or_conflict(exc) from exc


@router.get("/schedules", response_model=list[AutomationSchedule])
def list_schedules(context: TenantContext = Depends(tenant_context)) -> list[AutomationSchedule]:
    require_permission(context, Permission.AUTOMATION_READ)
    return engine.list_schedules(context)


@router.get("/schedules/{schedule_id}", response_model=AutomationSchedule)
def get_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_READ)
    try:
        return engine.get_schedule(context, schedule_id)
    except KeyError as exc:
        raise _not_found_or_conflict(exc) from exc


@router.put("/schedules/{schedule_id}", response_model=AutomationSchedule)
def update_schedule(schedule_id: str, payload: ScheduleUpdate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return engine.update_schedule(context, schedule_id, payload)
    except KeyError as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/schedules/{schedule_id}/{action}", response_model=AutomationSchedule)
def toggle_schedule(schedule_id: str, action: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="Schedule action not found")
    try:
        return engine.update_schedule(context, schedule_id, ScheduleUpdate(enabled=action == "enable"))
    except KeyError as exc:
        raise _not_found_or_conflict(exc) from exc


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        engine.delete_schedule(context, schedule_id)
    except KeyError as exc:
        raise _not_found_or_conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workflows/{workflow_id}/runs", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def start_run(workflow_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return engine.start(context, workflow_id, idempotency_key)
    except (KeyError, ValueError) as exc:
        raise _not_found_or_conflict(exc) from exc


@router.get("/runs", response_model=list[WorkflowRun])
def list_runs(context: TenantContext = Depends(tenant_context)) -> list[WorkflowRun]:
    require_permission(context, Permission.AUTOMATION_READ)
    return engine.list_runs(context)


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRun)
def cancel_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return engine.cancel(context, run_id)
    except (KeyError, ValueError) as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/runs/{run_id}/resume", response_model=WorkflowRun)
def resume_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        return engine.resume(context, run_id)
    except (KeyError, ValueError) as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/runs/{run_id}/retry", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def retry_run(run_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> WorkflowRun:
    require_permission(context, Permission.AUTOMATION_MANAGE)
    try:
        prior = engine.get_run(context, run_id)
        if prior.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError("Only failed or cancelled runs can be retried")
        return engine.start(context, prior.workflow_id, idempotency_key, retry_of=prior.id)
    except (KeyError, ValueError) as exc:
        raise _not_found_or_conflict(exc) from exc
