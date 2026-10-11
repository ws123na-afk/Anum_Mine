"""Automation workflows, schedules and runs (docs/automation.md).

Two engines implement one contract (``AutomationEngine``):

- ``LocalAutomationEngine`` keeps everything in a local SQLite file
  (``ANUM_AUTOMATION_DATABASE_PATH``). It is the engine for ``ANUM_REPOSITORY_BACKEND=memory``
  (local development and tests) and serves one process.
- ``PostgresAutomationEngine`` (``anum_api.db.automation_repository``) keeps them in the
  RLS-protected ``automation_*`` tables (migrations 0011 and 0031) when
  ``ANUM_REPOSITORY_BACKEND=postgresql``, so any number of API replicas share them.

A run executes its steps in process. Each run keeps a snapshot of the steps (action and
input) it started with, so editing a workflow never changes a run that is already paused.
Step actions do real work through ``RunEffects`` inside the engine's transaction:
``task.create`` creates an agent task, ``notify`` notifies the run's owner, ``pause`` waits
for someone to resume the run and ``fail`` stops it with a message. Finished, paused,
failed and cancelled runs publish ``automation_run.*`` events; a failure notifies the run's
owner and the workflow's creator when their ``automation_failed`` notification preference
is on.

``AutomationScheduler`` fires due schedules. With PostgreSQL it is safe on every replica:
due schedules are claimed with ``FOR UPDATE SKIP LOCKED`` and advanced in the same
transaction that records the run, and each fire has a unique idempotency key
(``schedule:<id>:<fire time>``), so a fire time never produces two runs.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from . import cron
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .schemas import DomainEvent, Task, TaskStatus, TenantContext, new_id, utc_now
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


ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED})


class OverlapPolicy(StrEnum):
    """What a new run does while another run of the same workflow is still active."""

    ALLOW = "allow"  # runs may overlap
    SKIP = "skip"  # at most one active run: a manual start is refused, a scheduled fire is skipped
    CANCEL_PREVIOUS = "cancel_previous"  # the new run cancels the active ones first


class ScheduleOutcome(StrEnum):
    FIRED = "fired"
    SKIPPED_OVERLAP = "skipped_overlap"
    WORKFLOW_PAUSED = "workflow_paused"
    WORKSPACE_ARCHIVED = "workspace_archived"
    OWNER_INACTIVE = "owner_inactive"


# The step actions the engine executes. Stored workflows may still hold older free-form
# actions (for example ``task.execute``); those run as recorded no-ops, but new and edited
# workflows must use one of these.
STEP_ACTIONS: dict[str, str] = {
    "task.create": "Create an agent task (input: title, prompt)",
    "notify": "Notify the run's owner in the app (input: message)",
    "pause": "Wait until someone resumes the run",
    "fail": "Stop the run as failed (input: message)",
}
TASK_TITLE_MAX = 160
TASK_PROMPT_MAX = 8000
MESSAGE_MAX = 500

MANAGE_DENIED = (
    "Changing or running automations needs the owner or member role in this workspace. "
    "Viewers can see automations and their runs; ask a workspace owner for the member role."
)
READ_DENIED = "Viewing automations needs a role in this workspace; ask a workspace owner to invite you."


class WorkflowStep(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=120)
    input: dict[str, Any] = Field(default_factory=dict)
    # Stored for a durable executor; the in-process executor runs each step once (retry the run).
    max_attempts: int = Field(default=3, ge=1, le=10)


def _step_label(index: int, step: WorkflowStep) -> str:
    return f'Step {index + 1} ("{step.name}")'


def _text_input(step: WorkflowStep, key: str) -> str:
    value = step.input.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def check_steps(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """The rules for new and edited workflows; every message says how to fix the step."""
    seen: set[str] = set()
    for index, step in enumerate(steps):
        label = _step_label(index, step)
        if step.id in seen:
            raise ValueError(f'{label}: step id "{step.id}" is used twice. Give every step a different id.')
        seen.add(step.id)
        if step.action not in STEP_ACTIONS:
            raise ValueError(
                f"{label}: action {step.action!r} is not available. Choose one of: {', '.join(STEP_ACTIONS)}."
            )
        if step.action == "task.create":
            title, prompt = _text_input(step, "title"), _text_input(step, "prompt")
            if not title or not prompt:
                raise ValueError(f'{label}: "Create agent task" needs a title and a prompt in its input.')
            if len(title) > TASK_TITLE_MAX:
                raise ValueError(f"{label}: the task title is longer than {TASK_TITLE_MAX} characters.")
            if len(prompt) > TASK_PROMPT_MAX:
                raise ValueError(f"{label}: the task prompt is longer than {TASK_PROMPT_MAX} characters.")
        if step.action in {"notify", "fail"}:
            message = step.input.get("message")
            if message is not None and not isinstance(message, str):
                raise ValueError(f"{label}: the message must be text.")
            if isinstance(message, str) and len(message) > MESSAGE_MAX:
                raise ValueError(f"{label}: the message is longer than {MESSAGE_MAX} characters.")
    return steps


class WorkflowFields(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=100)
    overlap_policy: OverlapPolicy = OverlapPolicy.ALLOW

    @field_validator("steps")
    @classmethod
    def unique_step_ids(cls, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        if len({step.id for step in steps}) != len(steps):
            raise ValueError("Workflow step IDs must be unique")
        return steps


class WorkflowCreate(WorkflowFields):
    @field_validator("name")
    @classmethod
    def named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the automation a name.")
        return value.strip()

    @field_validator("steps")
    @classmethod
    def valid_steps(cls, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        return check_steps(steps)


class WorkflowUpdate(WorkflowCreate):
    """``PUT /workflows/{id}``: the full new definition; the version goes up by one."""


SUMMARY_FIELDS = frozenset(
    {"last_run_at", "last_run_status", "next_run_at", "active_runs", "run_count", "schedule_count"}
)


class WorkflowDefinition(WorkflowFields):
    id: str
    tenant_id: str
    workspace_id: str
    status: WorkflowStatus
    version: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None
    # Summary, computed when listed or read (not stored with the definition).
    last_run_at: datetime | None = None
    last_run_status: RunStatus | None = None
    next_run_at: datetime | None = None
    active_runs: int = 0
    run_count: int = 0
    schedule_count: int = 0


def _valid_cron(value: str) -> str:
    try:
        cron.CronExpression.parse(value)
    except cron.CronError as exc:
        raise ValueError(str(exc)) from None
    return " ".join(value.split())


def _valid_timezone(value: str) -> str:
    try:
        cron.zone(value.strip())
    except cron.CronError as exc:
        raise ValueError(str(exc)) from None
    return value.strip()


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


class SchedulePreviewRequest(BaseModel):
    cron: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    count: int = Field(default=5, ge=1, le=10)

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        return _valid_cron(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _valid_timezone(value)


class SchedulePreview(BaseModel):
    cron: str
    timezone: str
    description: str
    next_runs: list[datetime]


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
    # What the scheduler did at the last fire time (ScheduleOutcome); null before the first.
    last_outcome: str | None = None
    created_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cron_description(self) -> str:
        try:
            return cron.describe(self.cron)
        except cron.CronError:
            return self.cron


class RunStepState(BaseModel):
    id: str
    name: str
    action: str
    status: RunStatus
    attempt: int = 0
    # The step's input when the run started (runs recorded before migration 0031 have none).
    input: dict[str, Any] = Field(default_factory=dict)
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
    # The workflow version the run executed (null for runs recorded before migration 0031).
    workflow_version: int | None = None
    # Filled in when listed or read, so history shows names after a workflow is renamed.
    workflow_name: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trigger(self) -> str:
        if self.schedule_id:
            return "schedule"
        if self.retry_of:
            return "retry"
        return "manual"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error(self) -> str | None:
        return next((step.error for step in self.steps if step.error), None)


class WorkflowFilter(BaseModel):
    query: str | None = None
    status: WorkflowStatus | None = None
    limit: int = 50
    offset: int = 0


class RunFilter(BaseModel):
    workflow_id: str | None = None
    schedule_id: str | None = None
    status: RunStatus | None = None
    limit: int = 50
    offset: int = 0


class AutomationNotFound(KeyError):
    """A workflow, schedule or run that does not exist in the caller's workspace."""

    def __init__(self, kind: str, item_id: str) -> None:
        super().__init__(item_id)
        self.message = f"No {kind} {item_id!r} in this workspace. It may have been deleted; refresh the list."


class AutomationConflict(ValueError):
    """The request is valid but the automation's current state refuses it (409)."""


class EditDenied(PermissionError):
    """The caller may manage automations but not this one (403)."""


class AutomationEngine(Protocol):
    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition: ...
    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]: ...
    def page_workflows(self, context: TenantContext, page: WorkflowFilter) -> tuple[list[WorkflowDefinition], int]: ...
    def get_workflow(self, context: TenantContext, workflow_id: str) -> WorkflowDefinition: ...
    def update_workflow(self, context: TenantContext, workflow_id: str, payload: WorkflowUpdate) -> WorkflowDefinition: ...
    def set_workflow_status(self, context: TenantContext, workflow_id: str, status: WorkflowStatus) -> WorkflowDefinition: ...
    def delete_workflow(self, context: TenantContext, workflow_id: str) -> None: ...
    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule: ...
    def list_schedules(self, context: TenantContext, workflow_id: str | None = None) -> list[AutomationSchedule]: ...
    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule: ...
    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule: ...
    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None: ...
    def start(self, context: TenantContext, workflow_id: str, idempotency_key: str | None = None, retry_of: str | None = None) -> WorkflowRun: ...
    def list_runs(self, context: TenantContext) -> list[WorkflowRun]: ...
    def page_runs(self, context: TenantContext, page: RunFilter) -> tuple[list[WorkflowRun], int]: ...
    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun: ...
    def run_due_schedules(self, *, limit: int = 100) -> list[WorkflowRun]: ...


# Effects: what steps and state changes do outside the automation tables ----------------


class RunEffects(Protocol):
    """Side effects of automation work, applied inside the engine's unit of work."""

    def create_task(self, context: TenantContext, title: str, prompt: str) -> Task: ...
    def record_event(self, event: DomainEvent) -> None: ...
    def record_audit(self, record: AuditRecord) -> None: ...
    def wants_failure_notice(self, context: TenantContext, user_id: str) -> bool: ...
    def actor_may_run(self, context: TenantContext, user_id: str) -> bool: ...
    def flush(self) -> None: ...


class RepositoryEffects:
    """``RunEffects`` over an ``AnumRepository`` (in-memory, or SQL in the engine's session).

    ``preferences`` reads one user's notification preferences in the run's workspace.
    Events are handed to the event bus by ``flush`` once the unit of work committed.
    """

    def __init__(self, repository: Any, preferences: Callable[[TenantContext], Any]) -> None:
        from .event_bus import EventCollectingRepository

        self.repository = EventCollectingRepository(repository)
        self._preferences = preferences

    def create_task(self, context: TenantContext, title: str, prompt: str) -> Task:
        now = utc_now()
        task = Task(
            id=new_id("task"),
            title=title,
            prompt=prompt,
            status=TaskStatus.CREATED,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            created_at=now,
            updated_at=now,
            created_by=context.user_id,
        )
        self.repository.create_task(task)
        self.repository.record_event(
            create_event(
                CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now
            ).event
        )
        return task

    def record_event(self, event: DomainEvent) -> None:
        self.repository.record_event(event)

    def record_audit(self, record: AuditRecord) -> None:
        self.repository.record_audit(record)

    def wants_failure_notice(self, context: TenantContext, user_id: str) -> bool:
        preferences = self._preferences(context.model_copy(update={"user_id": user_id}))
        return preferences is None or bool(preferences.automation_failed)

    def actor_may_run(self, context: TenantContext, user_id: str) -> bool:
        """With OIDC, a scheduled run's actor must still be an active owner or member."""
        if settings.auth_mode != "oidc" or user_id == SCHEDULER_ACTOR:
            return True
        membership = self.repository.get_membership(context.model_copy(update={"user_id": user_id}))
        return membership is not None and membership.active and membership.role.lower() in {"owner", "member"}

    def flush(self) -> None:
        from .dependencies import event_runtime

        events, self.repository.recorded_events = self.repository.recorded_events, []
        event_runtime.after_commit(events)


def memory_effects() -> RepositoryEffects:
    """Effects against the in-memory repository (``ANUM_REPOSITORY_BACKEND=memory``)."""
    from .dependencies import memory_repository
    from .onboarding import _notifications

    return RepositoryEffects(memory_repository, _notifications.get)


def audit(context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> AuditRecord:
    return AuditRecord(
        id=new_id("audit"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor=context.user_id,
        action=action,
        target=target,
        outcome="success",
        correlation_id=target,
        created_at=utc_now(),
        metadata=metadata or {},
    )


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
        workflow_name=workflow.name,
        workflow_version=workflow.version,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        status=RunStatus.QUEUED,
        idempotency_key=idempotency_key,
        retry_of=retry_of,
        schedule_id=schedule_id,
        created_by=context.user_id,
        steps=[
            RunStepState(id=step.id, name=step.name, action=step.action, input=dict(step.input), status=RunStatus.QUEUED)
            for step in workflow.steps
        ],
        created_at=now,
        updated_at=now,
    )


class StepFailed(Exception):
    """A step could not do its work; the run fails with this message."""


def _perform(state: RunStepState, run: WorkflowRun, context: TenantContext, effects: RunEffects) -> dict[str, Any]:
    if state.action == "task.create":
        title = str(state.input.get("title", "")).strip()[:TASK_TITLE_MAX]
        prompt = str(state.input.get("prompt", "")).strip()[:TASK_PROMPT_MAX]
        if not title or not prompt:
            raise StepFailed("The step's input has no task title or prompt. Edit the workflow and retry the run.")
        task = effects.create_task(context, title, prompt)
        return {"task_id": task.id, "title": task.title}
    if state.action == "notify":
        message = str(state.input.get("message") or f"{run.workflow_name or 'An automation'} reached \"{state.name}\".")
        recipients = [run.created_by] if run.created_by and run.created_by != SCHEDULER_ACTOR else []
        effects.record_event(
            create_event(
                CanonicalEventName.AUTOMATION_NOTIFICATION,
                context,
                run.id,
                {**_run_payload(run), "message": message[:MESSAGE_MAX], "notify_user_ids": recipients},
                correlation_id=run.id,
            ).event
        )
        return {"notified": recipients, "message": message[:MESSAGE_MAX]}
    # Older free-form actions are recorded as accepted; they have no executor.
    return {"accepted": True, "action": state.action}


def _run_payload(run: WorkflowRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_name": run.workflow_name,
        "schedule_id": run.schedule_id,
        "status": run.status.value,
        "trigger": run.trigger,
    }


def announce(run: WorkflowRun, context: TenantContext, effects: RunEffects, *, workflow_creator: str | None) -> None:
    """Publish the run's new state; a failure also names who should be notified."""
    names = {
        RunStatus.COMPLETED: CanonicalEventName.AUTOMATION_RUN_COMPLETED,
        RunStatus.PAUSED: CanonicalEventName.AUTOMATION_RUN_PAUSED,
        RunStatus.FAILED: CanonicalEventName.AUTOMATION_RUN_FAILED,
        RunStatus.CANCELLED: CanonicalEventName.AUTOMATION_RUN_CANCELLED,
    }
    name = names.get(run.status)
    if name is None:
        return
    payload = _run_payload(run)
    if run.status == RunStatus.FAILED:
        failed = next((step for step in run.steps if step.status == RunStatus.FAILED), None)
        candidates = [user for user in (run.created_by, workflow_creator) if user and user != SCHEDULER_ACTOR]
        payload.update(
            error=run.error,
            failed_step=failed.name if failed else None,
            notify_user_ids=[
                user for user in dict.fromkeys(candidates) if effects.wants_failure_notice(context, user)
            ],
        )
    effects.record_event(create_event(name, context, run.id, payload, correlation_id=run.id).event)


def execute_run(
    run: WorkflowRun,
    context: TenantContext,
    effects: RunEffects,
    *,
    workflow_creator: str | None = None,
) -> WorkflowRun:
    """Run steps from ``current_step`` until the run pauses, fails or completes."""
    run.status = RunStatus.RUNNING
    while run.current_step < len(run.steps):
        state = run.steps[run.current_step]
        state.attempt += 1
        if state.action == "pause":
            state.status = RunStatus.PAUSED
            run.status = RunStatus.PAUSED
            break
        if state.action == "fail":
            state.status = RunStatus.FAILED
            state.error = str(state.input.get("message") or "Step failed")
            run.status = RunStatus.FAILED
            break
        try:
            state.output = _perform(state, run, context, effects)
        except StepFailed as exc:
            state.status = RunStatus.FAILED
            state.error = str(exc)
            run.status = RunStatus.FAILED
            break
        state.status = RunStatus.COMPLETED
        run.current_step += 1
    if run.current_step == len(run.steps):
        run.status = RunStatus.COMPLETED
    run.updated_at = utc_now()
    announce(run, context, effects, workflow_creator=workflow_creator)
    return run


def fail_before_start(run: WorkflowRun, context: TenantContext, effects: RunEffects, message: str, *, workflow_creator: str | None) -> WorkflowRun:
    """Record a run that could not start (its first step carries the reason)."""
    if run.steps:
        run.steps[0].status = RunStatus.FAILED
        run.steps[0].error = message
    run.status = RunStatus.FAILED
    run.updated_at = utc_now()
    announce(run, context, effects, workflow_creator=workflow_creator)
    return run


def cancel_state(run: WorkflowRun) -> WorkflowRun:
    if run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
        raise AutomationConflict(f"This run is already {run.status.value}, so it cannot be cancelled.")
    if run.status == RunStatus.FAILED:
        raise AutomationConflict("This run already failed, so it cannot be cancelled. Retry it instead.")
    run.status = RunStatus.CANCELLED
    for step in run.steps[run.current_step:]:
        if step.status in ACTIVE_RUN_STATUSES:
            step.status = RunStatus.CANCELLED
    run.updated_at = utc_now()
    return run


def resume_state(run: WorkflowRun, context: TenantContext, effects: RunEffects, *, workflow_creator: str | None) -> WorkflowRun:
    if run.status != RunStatus.PAUSED:
        raise AutomationConflict(f"Only paused runs can be resumed; this run is {run.status.value}.")
    run.steps[run.current_step].status = RunStatus.COMPLETED
    run.steps[run.current_step].output = {"resumed": True, "resumed_by": context.user_id}
    run.current_step += 1
    return execute_run(run, context, effects, workflow_creator=workflow_creator)


def overlap_check(workflow: WorkflowDefinition, active: list[WorkflowRun]) -> None:
    """Raise ``AutomationConflict`` when the workflow's policy refuses another run now."""
    if workflow.status != WorkflowStatus.ACTIVE:
        raise AutomationConflict(f'"{workflow.name}" is paused. Resume the automation before running it.')
    if active and workflow.overlap_policy == OverlapPolicy.SKIP:
        first = active[0]
        raise AutomationConflict(
            f'"{workflow.name}" already has an active run ({first.id}, {first.status.value}) and its overlap '
            "policy is skip. Wait for that run to finish, cancel it, or allow overlapping runs."
        )


def _archived(context: TenantContext) -> bool:
    from .dependencies import workspace_archived

    return workspace_archived(context)


def scheduler_context(tenant_id: str, workspace_id: str, actor: str | None) -> TenantContext:
    """The explicit actor and tenant a scheduled run executes under."""
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor or SCHEDULER_ACTOR,
        roles=[],
    )


def owner_inactive_message(actor: str) -> str:
    return (
        f"The schedule's owner ({actor}) is no longer an active owner or member of this workspace, so it did "
        "not run. A workspace owner can delete the schedule and create it again under an active member."
    )


def is_owner(context: TenantContext) -> bool:
    return any(role.strip().lower() == "owner" for role in context.roles)


def require_editor(context: TenantContext, created_by: str | None, kind: str) -> None:
    """Members change their own automations and schedules; owners change any."""
    if created_by and created_by != context.user_id and not is_owner(context):
        raise EditDenied(
            f"Only the {kind}'s creator ({created_by}) or a workspace owner can change this {kind}."
        )


def matches(workflow: WorkflowDefinition, page: WorkflowFilter) -> bool:
    if page.status is not None and workflow.status != page.status:
        return False
    query = (page.query or "").strip().lower()
    return not query or query in workflow.name.lower() or query in workflow.description.lower()


def summarize(workflow: WorkflowDefinition, runs: list[WorkflowRun], schedules: list[AutomationSchedule]) -> WorkflowDefinition:
    """Fill in the run and schedule summary of one workflow (runs newest first)."""
    mine = [run for run in runs if run.workflow_id == workflow.id]
    timed = [s.next_run_at for s in schedules if s.workflow_id == workflow.id and s.enabled and s.next_run_at]
    return workflow.model_copy(
        update={
            "last_run_at": mine[0].created_at if mine else None,
            "last_run_status": mine[0].status if mine else None,
            "next_run_at": min(timed) if timed and workflow.status == WorkflowStatus.ACTIVE else None,
            "active_runs": sum(1 for run in mine if run.status in ACTIVE_RUN_STATUSES),
            "run_count": len(mine),
            "schedule_count": sum(1 for s in schedules if s.workflow_id == workflow.id),
        }
    )


# Local SQLite engine -------------------------------------------------------------------

# Every statement is a fixed string per table: nothing is interpolated into SQL.
_SAVE = {
    "automation_workflows": "insert or replace into automation_workflows (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
    "automation_schedules": "insert or replace into automation_schedules (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
}
_LIST = {
    "automation_workflows": "select body from automation_workflows where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
    "automation_schedules": "select body from automation_schedules where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
    "automation_runs": "select body from automation_runs where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
}
_DELETE = {
    "automation_workflows": "delete from automation_workflows where id = ? and tenant_id = ? and workspace_id = ?",
    "automation_schedules": "delete from automation_schedules where id = ? and tenant_id = ? and workspace_id = ?",
}
_DELETE_RUNS_OF = "delete from automation_runs where workflow_id = ? and tenant_id = ? and workspace_id = ?"
_DELETE_SCHEDULES_OF = (
    "delete from automation_schedules where tenant_id = ? and workspace_id = ? "
    "and json_extract(body, '$.workflow_id') = ?"
)
_SAVE_RUN = (
    "insert or replace into automation_runs (id, tenant_id, workspace_id, workflow_id, idempotency_key, body, created_at) "
    "values (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_RUN_ONCE = (
    "insert or ignore into automation_runs (id, tenant_id, workspace_id, workflow_id, idempotency_key, body, created_at) "
    "values (?, ?, ?, ?, ?, ?, ?)"
)
_RUN_BY_KEY = "select body from automation_runs where tenant_id = ? and workspace_id = ? and idempotency_key = ?"
_ALL_SCHEDULES = "select tenant_id, workspace_id, body from automation_schedules"


class LocalAutomationEngine:
    """SQLite orchestration backend implementing the Temporal-facing contract."""

    def __init__(self, database_path: str, effects_factory: Callable[[], RunEffects] = memory_effects) -> None:
        self.database_path = database_path
        self.effects_factory = effects_factory
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

    @contextmanager
    def _tx(self) -> Iterator[tuple[sqlite3.Connection, RunEffects]]:
        """One write transaction (SQLite's write lock held); effects flush after commit."""
        with self._lock:
            connection = self._connect()
            effects = self.effects_factory()
            try:
                connection.execute("begin immediate")
                yield connection, effects
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
            effects.flush()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _scope(context: TenantContext) -> tuple[str, str]:
        return context.tenant_id, context.workspace_id

    @staticmethod
    def _rows(connection: sqlite3.Connection, table: str, model: type[BaseModel], context: TenantContext) -> list[Any]:
        rows = connection.execute(_LIST[table], (context.tenant_id, context.workspace_id)).fetchall()
        return [model.model_validate_json(row["body"]) for row in rows]

    @staticmethod
    def _put(connection: sqlite3.Connection, table: str, value: WorkflowDefinition | AutomationSchedule) -> None:
        computed = set(SUMMARY_FIELDS) if isinstance(value, WorkflowDefinition) else {"cron_description"}
        body = value.model_dump_json(exclude=computed)
        connection.execute(_SAVE[table], (value.id, value.tenant_id, value.workspace_id, body, value.created_at.isoformat()))

    @staticmethod
    def _run_values(run: WorkflowRun) -> tuple[Any, ...]:
        return (
            run.id,
            run.tenant_id,
            run.workspace_id,
            run.workflow_id,
            run.idempotency_key,
            run.model_dump_json(exclude={"trigger", "error", "workflow_name"}),
            run.created_at.isoformat(),
        )

    def _workflow(self, connection: sqlite3.Connection, context: TenantContext, workflow_id: str) -> WorkflowDefinition:
        workflow = next(
            (item for item in self._rows(connection, "automation_workflows", WorkflowDefinition, context) if item.id == workflow_id),
            None,
        )
        if workflow is None:
            raise AutomationNotFound("automation", workflow_id)
        return workflow

    def _schedule(self, connection: sqlite3.Connection, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        schedule = next(
            (item for item in self._rows(connection, "automation_schedules", AutomationSchedule, context) if item.id == schedule_id),
            None,
        )
        if schedule is None:
            raise AutomationNotFound("schedule", schedule_id)
        return schedule

    def _runs(self, connection: sqlite3.Connection, context: TenantContext) -> list[WorkflowRun]:
        runs = self._rows(connection, "automation_runs", WorkflowRun, context)
        names = {w.id: w.name for w in self._rows(connection, "automation_workflows", WorkflowDefinition, context)}
        for run in runs:
            run.workflow_name = names.get(run.workflow_id)
        return runs

    def _run(self, connection: sqlite3.Connection, context: TenantContext, run_id: str) -> WorkflowRun:
        run = next((item for item in self._runs(connection, context) if item.id == run_id), None)
        if run is None:
            raise AutomationNotFound("run", run_id)
        return run

    # -- workflows ------------------------------------------------------------------------

    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition:
        now = utc_now()
        workflow = WorkflowDefinition(
            id=new_id("workflow"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            status=WorkflowStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
            created_by=context.user_id,
            **payload.model_dump(),
        )
        with self._tx() as (connection, effects):
            self._put(connection, "automation_workflows", workflow)
            effects.record_audit(audit(context, "automation.workflow.created", workflow.id, {"name": workflow.name}))
        return workflow

    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]:
        return self.page_workflows(context, WorkflowFilter(limit=10_000))[0]

    def page_workflows(self, context: TenantContext, page: WorkflowFilter) -> tuple[list[WorkflowDefinition], int]:
        with self._read() as connection:
            workflows = [w for w in self._rows(connection, "automation_workflows", WorkflowDefinition, context) if matches(w, page)]
            runs = self._rows(connection, "automation_runs", WorkflowRun, context)
            schedules = self._rows(connection, "automation_schedules", AutomationSchedule, context)
        selected = workflows[page.offset : page.offset + page.limit]
        return [summarize(w, runs, schedules) for w in selected], len(workflows)

    def get_workflow(self, context: TenantContext, workflow_id: str) -> WorkflowDefinition:
        with self._read() as connection:
            workflow = self._workflow(connection, context, workflow_id)
            runs = self._rows(connection, "automation_runs", WorkflowRun, context)
            schedules = self._rows(connection, "automation_schedules", AutomationSchedule, context)
        return summarize(workflow, runs, schedules)

    def update_workflow(self, context: TenantContext, workflow_id: str, payload: WorkflowUpdate) -> WorkflowDefinition:
        with self._tx() as (connection, effects):
            current = self._workflow(connection, context, workflow_id)
            require_editor(context, current.created_by, "automation")
            updated = WorkflowDefinition.model_validate(
                {
                    **current.model_dump(exclude=set(SUMMARY_FIELDS)),
                    **payload.model_dump(),
                    "version": current.version + 1,
                    "updated_at": utc_now(),
                    "updated_by": context.user_id,
                }
            )
            self._put(connection, "automation_workflows", updated)
            effects.record_audit(
                audit(context, "automation.workflow.updated", workflow_id, {"name": updated.name, "version": updated.version})
            )
        return self.get_workflow(context, workflow_id)

    def set_workflow_status(self, context: TenantContext, workflow_id: str, status: WorkflowStatus) -> WorkflowDefinition:
        with self._tx() as (connection, effects):
            current = self._workflow(connection, context, workflow_id)
            require_editor(context, current.created_by, "automation")
            if current.status != status:
                updated = current.model_copy(update={"status": status, "updated_at": utc_now(), "updated_by": context.user_id})
                self._put(connection, "automation_workflows", updated)
                action = "automation.workflow.resumed" if status == WorkflowStatus.ACTIVE else "automation.workflow.paused"
                effects.record_audit(audit(context, action, workflow_id, {"name": current.name}))
        return self.get_workflow(context, workflow_id)

    def delete_workflow(self, context: TenantContext, workflow_id: str) -> None:
        with self._tx() as (connection, effects):
            workflow = self._workflow(connection, context, workflow_id)
            require_editor(context, workflow.created_by, "automation")
            runs = [run for run in self._runs(connection, context) if run.workflow_id == workflow_id]
            active = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]
            if active:
                raise AutomationConflict(
                    f'"{workflow.name}" has {len(active)} active run(s). Cancel them first, then delete the automation.'
                )
            scope = self._scope(context)
            connection.execute(_DELETE_SCHEDULES_OF, (*scope, workflow_id))
            connection.execute(_DELETE_RUNS_OF, (workflow_id, *scope))
            connection.execute(_DELETE["automation_workflows"], (workflow_id, *scope))
            effects.record_audit(
                audit(context, "automation.workflow.deleted", workflow_id, {"name": workflow.name, "runs_deleted": len(runs)})
            )

    # -- schedules ------------------------------------------------------------------------

    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule:
        now = utc_now()
        with self._tx() as (connection, effects):
            self._workflow(connection, context, payload.workflow_id)
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
            self._put(connection, "automation_schedules", schedule)
            effects.record_audit(
                audit(context, "automation.schedule.created", schedule.id, {"workflow_id": schedule.workflow_id, "cron": schedule.cron})
            )
        return schedule

    def list_schedules(self, context: TenantContext, workflow_id: str | None = None) -> list[AutomationSchedule]:
        with self._read() as connection:
            schedules = self._rows(connection, "automation_schedules", AutomationSchedule, context)
        return [s for s in schedules if workflow_id is None or s.workflow_id == workflow_id]

    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        with self._read() as connection:
            return self._schedule(connection, context, schedule_id)

    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule:
        with self._tx() as (connection, effects):
            schedule = self._schedule(connection, context, schedule_id)
            require_editor(context, schedule.created_by, "schedule")
            updates = payload.model_dump(exclude_none=True)
            now = utc_now()
            updated = schedule.model_copy(update={**updates, "updated_at": now})
            cron.validate(updated.cron, updated.timezone)
            if updated.enabled and (not schedule.enabled or {"cron", "timezone"} & updates.keys()):
                updated.next_run_at = next_fire(updated.cron, updated.timezone, now)
            elif not updated.enabled:
                updated.next_run_at = None
            self._put(connection, "automation_schedules", updated)
            effects.record_audit(audit(context, "automation.schedule.updated", schedule_id, dict(updates)))
            return updated

    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None:
        with self._tx() as (connection, effects):
            schedule = self._schedule(connection, context, schedule_id)
            require_editor(context, schedule.created_by, "schedule")
            connection.execute(_DELETE["automation_schedules"], (schedule_id, *self._scope(context)))
            effects.record_audit(audit(context, "automation.schedule.deleted", schedule_id, {"name": schedule.name}))

    # -- runs -----------------------------------------------------------------------------

    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._read() as connection:
            return self._run(connection, context, run_id)

    def _existing_by_key(self, connection: sqlite3.Connection, context: TenantContext, key: str) -> WorkflowRun | None:
        row = connection.execute(_RUN_BY_KEY, (*self._scope(context), key)).fetchone()
        return WorkflowRun.model_validate_json(row["body"]) if row else None

    def _launch(
        self,
        connection: sqlite3.Connection,
        effects: RunEffects,
        context: TenantContext,
        workflow: WorkflowDefinition,
        *,
        idempotency_key: str | None,
        retry_of: str | None = None,
        schedule_id: str | None = None,
    ) -> WorkflowRun | None:
        """Claim the idempotency key, apply the overlap policy and execute. None: key taken."""
        active = [r for r in self._runs(connection, context) if r.workflow_id == workflow.id and r.status in ACTIVE_RUN_STATUSES]
        run = new_run(context, workflow, idempotency_key=idempotency_key, retry_of=retry_of, schedule_id=schedule_id)
        if not connection.execute(_INSERT_RUN_ONCE, self._run_values(run)).rowcount:
            return None
        if workflow.overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            for previous in active:
                cancel_state(previous)
                connection.execute(_SAVE_RUN, self._run_values(previous))
                announce(previous, context, effects, workflow_creator=workflow.created_by)
        execute_run(run, context, effects, workflow_creator=workflow.created_by)
        connection.execute(_SAVE_RUN, self._run_values(run))
        return run

    def start(self, context: TenantContext, workflow_id: str, idempotency_key: str | None = None, retry_of: str | None = None) -> WorkflowRun:
        with self._tx() as (connection, effects):
            if idempotency_key:
                existing = self._existing_by_key(connection, context, idempotency_key)
                if existing:
                    return existing
            workflow = self._workflow(connection, context, workflow_id)
            active = [r for r in self._runs(connection, context) if r.workflow_id == workflow_id and r.status in ACTIVE_RUN_STATUSES]
            overlap_check(workflow, active)
            run = self._launch(connection, effects, context, workflow, idempotency_key=idempotency_key, retry_of=retry_of)
            if run is None:  # pragma: no cover - the key was checked under the write lock
                raise AutomationConflict("A run with this idempotency key already exists.")
            return run

    def list_runs(self, context: TenantContext) -> list[WorkflowRun]:
        with self._read() as connection:
            return self._runs(connection, context)

    def page_runs(self, context: TenantContext, page: RunFilter) -> tuple[list[WorkflowRun], int]:
        runs = [
            run
            for run in self.list_runs(context)
            if (page.workflow_id is None or run.workflow_id == page.workflow_id)
            and (page.schedule_id is None or run.schedule_id == page.schedule_id)
            and (page.status is None or run.status == page.status)
        ]
        return runs[page.offset : page.offset + page.limit], len(runs)

    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._tx() as (connection, effects):
            run = cancel_state(self._run(connection, context, run_id))
            connection.execute(_SAVE_RUN, self._run_values(run))
            workflow = self._workflow(connection, context, run.workflow_id)
            announce(run, context, effects, workflow_creator=workflow.created_by)
            return run

    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._tx() as (connection, effects):
            run = self._run(connection, context, run_id)
            workflow = self._workflow(connection, context, run.workflow_id)
            run = resume_state(run, context, effects, workflow_creator=workflow.created_by)
            connection.execute(_SAVE_RUN, self._run_values(run))
            return run

    def run_due_schedules(self, *, limit: int = 100, now: datetime | None = None) -> list[WorkflowRun]:
        """Fire every due schedule once; a missed fire runs once, it does not catch up.

        ``BEGIN IMMEDIATE`` holds SQLite's write lock for the whole pass, so two local
        processes sharing the file cannot fire the same schedule twice either.
        """
        now = now or utc_now()
        runs: list[WorkflowRun] = []
        with self._tx() as (connection, effects):
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
                try:
                    workflow: WorkflowDefinition | None = self._workflow(connection, context, schedule.workflow_id)
                except AutomationNotFound:
                    workflow = None
                key = schedule_fire_key(schedule.id, fire_at)
                outcome = ScheduleOutcome.FIRED
                # An archived workspace is read-only: its schedules advance without running.
                if _archived(context):
                    outcome = ScheduleOutcome.WORKSPACE_ARCHIVED
                elif workflow is None or workflow.status != WorkflowStatus.ACTIVE:
                    outcome = ScheduleOutcome.WORKFLOW_PAUSED
                elif not effects.actor_may_run(context, context.user_id):
                    outcome = ScheduleOutcome.OWNER_INACTIVE
                    run = new_run(context, workflow, idempotency_key=key, schedule_id=schedule.id)
                    if connection.execute(_INSERT_RUN_ONCE, self._run_values(run)).rowcount:
                        fail_before_start(run, context, effects, owner_inactive_message(context.user_id), workflow_creator=workflow.created_by)
                        connection.execute(_SAVE_RUN, self._run_values(run))
                        runs.append(run)
                else:
                    active = [
                        r for r in self._runs(connection, context) if r.workflow_id == workflow.id and r.status in ACTIVE_RUN_STATUSES
                    ]
                    if active and workflow.overlap_policy == OverlapPolicy.SKIP:
                        outcome = ScheduleOutcome.SKIPPED_OVERLAP
                    else:
                        fired = self._launch(connection, effects, context, workflow, idempotency_key=key, schedule_id=schedule.id)
                        if fired is not None:
                            runs.append(fired)
                schedule.last_run_at = now
                schedule.last_outcome = outcome.value
                schedule.next_run_at = next_fire(schedule.cron, schedule.timezone, max(now, fire_at))
                schedule.updated_at = now
                self._put(connection, "automation_schedules", schedule)
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
TOTAL_COUNT_HEADER = "X-Total-Count"


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeNotProvisionedError):
        return HTTPException(status_code=409, detail="Complete onboarding for this workspace first (Settings, Workspace onboarding).")
    if isinstance(exc, AutomationNotFound):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=f"No automation item {exc.args[0]!r} in this workspace.")
    if isinstance(exc, EditDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, cron.CronError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


_HANDLED = (KeyError, ValueError, ScopeNotProvisionedError, EditDenied)


def _require(context: TenantContext, permission: Permission) -> None:
    """``require_permission`` with a refusal that says who may do it."""
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            detail = READ_DENIED if permission == Permission.AUTOMATION_READ else MANAGE_DENIED
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from None
        raise


@contextmanager
def _handled() -> Iterator[None]:
    try:
        yield
    except _HANDLED as exc:
        raise _http_error(exc) from exc


@router.post("/workflows", response_model=WorkflowDefinition, status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreate, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().create_workflow(context, payload)


@router.get("/workflows", response_model=list[WorkflowDefinition])
def list_workflows(
    response: Response,
    query: str | None = Query(default=None, max_length=160),
    workflow_status: WorkflowStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[WorkflowDefinition]:
    _require(context, Permission.AUTOMATION_READ)
    items, total = current_engine().page_workflows(
        context, WorkflowFilter(query=query, status=workflow_status, limit=limit, offset=offset)
    )
    response.headers[TOTAL_COUNT_HEADER] = str(total)
    return items


@router.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
def get_workflow(workflow_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    _require(context, Permission.AUTOMATION_READ)
    with _handled():
        return current_engine().get_workflow(context, workflow_id)


@router.put("/workflows/{workflow_id}", response_model=WorkflowDefinition)
def update_workflow(workflow_id: str, payload: WorkflowUpdate, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().update_workflow(context, workflow_id, payload)


def _set_status(context: TenantContext, workflow_id: str, target: WorkflowStatus) -> WorkflowDefinition:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().set_workflow_status(context, workflow_id, target)


@router.post("/workflows/{workflow_id}/enable", response_model=WorkflowDefinition)
def enable_workflow(workflow_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    """Resume a paused automation: manual runs and its schedules work again."""
    return _set_status(context, workflow_id, WorkflowStatus.ACTIVE)


@router.post("/workflows/{workflow_id}/disable", response_model=WorkflowDefinition)
def disable_workflow(workflow_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowDefinition:
    """Pause an automation: manual runs are refused and its schedules skip their fire times."""
    return _set_status(context, workflow_id, WorkflowStatus.DISABLED)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_workflow(workflow_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        current_engine().delete_workflow(context, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/schedules/preview", response_model=SchedulePreview)
def preview_schedule(payload: SchedulePreviewRequest, context: TenantContext = Depends(tenant_context)) -> SchedulePreview:
    """The schedule in words and its next fire times, before it is saved."""
    _require(context, Permission.AUTOMATION_READ)
    with _handled():
        return SchedulePreview(
            cron=payload.cron,
            timezone=payload.timezone,
            description=cron.describe(payload.cron),
            next_runs=cron.upcoming(payload.cron, payload.timezone, count=payload.count),
        )


@router.post("/schedules", response_model=AutomationSchedule, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().create_schedule(context, payload)


@router.get("/schedules", response_model=list[AutomationSchedule])
def list_schedules(
    response: Response,
    workflow_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[AutomationSchedule]:
    _require(context, Permission.AUTOMATION_READ)
    schedules = current_engine().list_schedules(context, workflow_id)
    response.headers[TOTAL_COUNT_HEADER] = str(len(schedules))
    return schedules[offset : offset + limit]


@router.get("/schedules/{schedule_id}", response_model=AutomationSchedule)
def get_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    _require(context, Permission.AUTOMATION_READ)
    with _handled():
        return current_engine().get_schedule(context, schedule_id)


@router.put("/schedules/{schedule_id}", response_model=AutomationSchedule)
def update_schedule(schedule_id: str, payload: ScheduleUpdate, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().update_schedule(context, schedule_id, payload)


@router.post("/schedules/{schedule_id}/{action}", response_model=AutomationSchedule)
def toggle_schedule(schedule_id: str, action: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    _require(context, Permission.AUTOMATION_MANAGE)
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="Schedule action not found; use enable or disable.")
    with _handled():
        return current_engine().update_schedule(context, schedule_id, ScheduleUpdate(enabled=action == "enable"))


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        current_engine().delete_schedule(context, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workflows/{workflow_id}/runs", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def start_run(workflow_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200)) -> WorkflowRun:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().start(context, workflow_id, idempotency_key)


@router.get("/runs", response_model=list[WorkflowRun])
def list_runs(
    response: Response,
    workflow_id: str | None = Query(default=None, max_length=80),
    schedule_id: str | None = Query(default=None, max_length=80),
    run_status: RunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[WorkflowRun]:
    _require(context, Permission.AUTOMATION_READ)
    items, total = current_engine().page_runs(
        context, RunFilter(workflow_id=workflow_id, schedule_id=schedule_id, status=run_status, limit=limit, offset=offset)
    )
    response.headers[TOTAL_COUNT_HEADER] = str(total)
    return items


@router.get("/runs/{run_id}", response_model=WorkflowRun)
def get_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    _require(context, Permission.AUTOMATION_READ)
    with _handled():
        return current_engine().get_run(context, run_id)


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRun)
def cancel_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().cancel(context, run_id)


@router.post("/runs/{run_id}/resume", response_model=WorkflowRun)
def resume_run(run_id: str, context: TenantContext = Depends(tenant_context)) -> WorkflowRun:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().resume(context, run_id)


@router.post("/runs/{run_id}/retry", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
def retry_run(run_id: str, context: TenantContext = Depends(tenant_context), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200)) -> WorkflowRun:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        selected = current_engine()
        prior = selected.get_run(context, run_id)
        if prior.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise AutomationConflict(f"Only failed or cancelled runs can be retried; this run is {prior.status.value}.")
        return selected.start(context, prior.workflow_id, idempotency_key, retry_of=prior.id)
