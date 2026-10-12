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
``task.create`` creates an agent task and hands it to the ``AutomationDispatcher``
(``automation_dispatch.py``), which runs it through the agent runtime after the
transaction commits; the step waits (``waiting_for``: ``task``, then ``approval`` while
ToolPolicy holds it for a person) until the task finishes, and its result is available to
later steps as ``{{steps.<id>.result}}``. ``email.send`` asks to send an email from a
connected mailbox (always approved by a person first), ``notify`` notifies the run's owner
in the app and on their phone, ``pause`` waits for someone to resume the run and ``fail``
stops it with a message. A failed step is retried in place with exponential backoff up to
its ``max_attempts``. Finished, paused, failed and cancelled runs publish
``automation_run.*`` events; a failure notifies the run's owner and the workflow's creator
when their ``automation_failed`` notification preference is on.

Runs start manually, on a schedule, or from a trigger (``automation_triggers.py``): an
event trigger when a matching domain event is committed, a webhook trigger when a signed
request arrives.

``AutomationScheduler`` fires due schedules. With PostgreSQL it is safe on every replica:
due schedules are claimed with ``FOR UPDATE SKIP LOCKED`` and advanced in the same
transaction that records the run, and each fire has a unique idempotency key
(``schedule:<id>:<fire time>``), so a fire time never produces two runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from . import cron
from .audit import AuditRecord
from .automation_triggers import (
    TRIGGER_EVENTS,
    AutomationTrigger,
    TriggerCreate,
    TriggerKind,
    TriggerOutcome,
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    WEBHOOK_BODY_MAX_BYTES,
    SignatureError,
    TriggerEvent,
    TriggerUpdate,
    TriggerWithSecret,
    WebhookAccepted,
    bounded,
    conditions_match,
    delivery_id,
    event_fire_key,
    event_payload,
    event_task_id,
    new_secret,
    secret_hint,
    verify,
    webhook_fire_key,
)
from .hardening import InMemoryTokenBucket, client_key
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .schemas import ApprovalStatus, DomainEvent, Task, TaskStatus, TenantContext, new_id, utc_now
from .scoped_store import ScopeNotProvisionedError
from .settings import settings

logger = logging.getLogger(__name__)

SCHEDULER_ACTOR = "system:automation-scheduler"
# The step that stores a generated report (anum_api/reports.py, docs/reports.md).
REPORT_STEP_ACTION = "report.generate"


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
    "task.create": "Create an agent task and run it (input: title, prompt; run: false only creates it)",
    "email.send": "Send an email from a connected mailbox once a person approves it (input: account_id, to, subject, body)",
    "notify": "Notify the run's owner (input: message; channels: in_app, push)",
    "pause": "Wait until someone resumes the run",
    "fail": "Stop the run as failed (input: message)",
    # Tyre planning (docs/tyre-planning.md#reports): runs after the run is committed.
    "tyre.report": "Sync the tyre data and generate a tyre report (input: kind, optional delivery)",
    REPORT_STEP_ACTION: "Store a shared report (input: type task_summary, automation_summary, commerce_summary or tyre_summary; period_days 1-366)",
}
TASK_TITLE_MAX = 160
TASK_PROMPT_MAX = 8000
MESSAGE_MAX = 500
EMAIL_SUBJECT_MAX = 500
EMAIL_BODY_MAX = 20_000
EMAIL_RECIPIENTS_MAX = 20
NOTIFY_CHANNELS = ("in_app", "push")
RESULT_MAX = 4000
# A step waiting on a task is looked at again at least this often (the event that finishes
# the task normally moves it at once); a task waiting for approval less often.
RECONCILE_SECONDS = 60
RECONCILE_APPROVAL_SECONDS = 900
# A deferred step whose work has not started after this long is handed out again.
STALE_DEFER_SECONDS = 120
RETRY_DELAY_CAP_SECONDS = 6 * 3600

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
    # A failed step is tried again in place up to this many attempts in all, waiting
    # retry_delay_seconds * 2^(attempt - 1) (at most six hours) between attempts.
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_delay_seconds: int = Field(default=60, ge=5, le=3600)


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
            run_flag = step.input.get("run", True)
            if not isinstance(run_flag, bool):
                raise ValueError(f'{label}: "run" must be true (run the task) or false (only create it).')
        if step.action == "email.send":
            _check_email_step(label, step)
        if step.action == REPORT_STEP_ACTION:
            kind, days = step.input.get("type", "task_summary"), step.input.get("period_days", 7)
            if kind not in {"task_summary", "automation_summary", "commerce_summary", "tyre_summary"}:
                raise ValueError(
                    f'{label}: the report type must be "task_summary", "automation_summary", "commerce_summary" or "tyre_summary".'
                )
            if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
                raise ValueError(f"{label}: period_days must be a whole number from 1 to 366.")
        if step.action in {"notify", "fail"}:
            message = step.input.get("message")
            if message is not None and not isinstance(message, str):
                raise ValueError(f"{label}: the message must be text.")
            if isinstance(message, str) and len(message) > MESSAGE_MAX:
                raise ValueError(f"{label}: the message is longer than {MESSAGE_MAX} characters.")
        if step.action == "notify":
            channels = step.input.get("channels", ["in_app"])
            if (
                not isinstance(channels, list)
                or not channels
                or any(channel not in NOTIFY_CHANNELS for channel in channels)
            ):
                raise ValueError(f"{label}: channels must list one or more of: {', '.join(NOTIFY_CHANNELS)}.")
    return steps


def _check_email_step(label: str, step: WorkflowStep) -> None:
    account_id = _text_input(step, "account_id")
    if not account_id:
        raise ValueError(f'{label}: "Send email" needs the mailbox to send from (account_id). Connect one under Email first.')
    to = step.input.get("to")
    recipients = [to] if isinstance(to, str) else to
    if (
        not isinstance(recipients, list)
        or not any(isinstance(item, str) and item.strip() for item in recipients)
        or not all(isinstance(item, str) for item in recipients)
    ):
        raise ValueError(f'{label}: "Send email" needs at least one recipient in "to".')
    if len(recipients) > EMAIL_RECIPIENTS_MAX:
        raise ValueError(f"{label}: send to at most {EMAIL_RECIPIENTS_MAX} recipients.")
    subject, body = step.input.get("subject", ""), step.input.get("body")
    if not isinstance(subject, str) or len(subject) > EMAIL_SUBJECT_MAX:
        raise ValueError(f"{label}: the subject must be text of at most {EMAIL_SUBJECT_MAX} characters.")
    if not isinstance(body, str) or not body.strip():
        raise ValueError(f'{label}: "Send email" needs a body.')
    if len(body) > EMAIL_BODY_MAX:
        raise ValueError(f"{label}: the body is longer than {EMAIL_BODY_MAX} characters.")


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
    description_ar: str
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cron_description_ar(self) -> str:
        try:
            return cron.describe(self.cron, "ar")
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
    # Snapshot of the step's retry policy (older runs: one attempt).
    max_attempts: int = 1
    retry_delay_seconds: int = 60
    # While the step is running: what it waits for. ``start`` (its work is queued),
    # ``task`` (the agent task is running), ``approval`` (a person must approve the task's
    # action) or ``retry`` (the last attempt failed; the next starts at next_attempt_at).
    waiting_for: str | None = None
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None


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
    # Set when a trigger started the run: the trigger, the event type (or "webhook") and
    # what it delivered, available to steps as {{trigger.<field>}}.
    trigger_id: str | None = None
    trigger_type: str | None = None
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    # When the engine should look at the run again (a retry is due, a waiting step is
    # re-checked); null when nothing is pending. The agent task the current step waits on.
    wake_at: datetime | None = None
    waiting_task_id: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trigger(self) -> str:
        if self.schedule_id:
            return "schedule"
        if self.trigger_id:
            return "webhook" if self.trigger_type == "webhook" else "event"
        if self.retry_of:
            return "retry"
        return "manual"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def waiting_for(self) -> str | None:
        if self.status != RunStatus.RUNNING or self.current_step >= len(self.steps):
            return None
        return self.steps[self.current_step].waiting_for

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
    trigger_id: str | None = None
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
    def take_over_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule: ...
    # Waiting steps and retries.
    def advance(self, context: TenantContext, run_id: str, report: StepReport | None = None) -> WorkflowRun: ...
    def wake_for_task(self, context: TenantContext, task_id: str) -> list[WorkflowRun]: ...
    def run_due_wakeups(self, *, limit: int = 100) -> list[WorkflowRun]: ...
    # Triggers.
    def create_trigger(self, context: TenantContext, payload: TriggerCreate) -> tuple[AutomationTrigger, str | None]: ...
    def list_triggers(self, context: TenantContext, workflow_id: str | None = None) -> list[AutomationTrigger]: ...
    def get_trigger(self, context: TenantContext, trigger_id: str) -> AutomationTrigger: ...
    def update_trigger(self, context: TenantContext, trigger_id: str, payload: TriggerUpdate) -> AutomationTrigger: ...
    def delete_trigger(self, context: TenantContext, trigger_id: str) -> None: ...
    def rotate_trigger_secret(self, context: TenantContext, trigger_id: str) -> tuple[AutomationTrigger, str]: ...
    def take_over_trigger(self, context: TenantContext, trigger_id: str) -> AutomationTrigger: ...
    def trigger_record(self, context: TenantContext, trigger_id: str) -> AutomationTrigger: ...
    def fire_trigger(
        self, context: TenantContext, trigger_id: str, *, key: str, payload: dict[str, Any], source_task_id: str | None = None
    ) -> tuple[WorkflowRun | None, str]: ...
    def fire_event(self, context: TenantContext, event: DomainEvent) -> list[WorkflowRun]: ...
    def catch_up_triggers(self, *, limit: int = 100) -> list[WorkflowRun]: ...


# Effects: what steps and state changes do outside the automation tables ----------------


@dataclass(frozen=True)
class DeferredStep:
    """Step work that runs after the engine's transaction commits (``AutomationDispatcher``).

    ``task.create``: run agent task ``task_id`` as ``actor``. ``email.send``: ask to send
    ``arguments`` (account_id, to, subject, body) from a connected mailbox.
    """

    tenant_id: str
    workspace_id: str
    actor: str
    run_id: str
    workflow_id: str
    step_id: str
    attempt: int
    action: str
    task_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepReport:
    """What deferred step work reports back to ``AutomationEngine.advance``."""

    step_id: str
    attempt: int
    output: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    error: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class TaskOutcome:
    status: TaskStatus
    updated_at: datetime
    result: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None
    error: str | None = None


class RunEffects(Protocol):
    """Side effects of automation work, applied inside the engine's unit of work."""

    def create_task(self, context: TenantContext, title: str, prompt: str) -> Task: ...
    def record_event(self, event: DomainEvent) -> None: ...
    def record_audit(self, record: AuditRecord) -> None: ...
    def wants_failure_notice(self, context: TenantContext, user_id: str) -> bool: ...
    def actor_may_run(self, context: TenantContext, user_id: str) -> bool: ...
    def task_outcome(self, context: TenantContext, task_id: str) -> TaskOutcome | None: ...
    def events_since(self, context: TenantContext, event_type: str, after: datetime, limit: int) -> list[DomainEvent]: ...
    def defer(self, job: DeferredStep) -> None: ...
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
        self.deferred: list[DeferredStep] = []

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

    def task_outcome(self, context: TenantContext, task_id: str) -> TaskOutcome | None:
        task = self.repository.get_task(task_id, context)
        if task is None:
            return None
        run = self.repository.find_run_for_task(task_id, context)
        approval = None
        if run is not None and run.checkpoint.approval_id:
            approval = self.repository.get_approval(run.checkpoint.approval_id, context)
        error = None
        if task.status == TaskStatus.FAILED and run is not None and run.steps:
            error = run.steps[-1].summary
        return TaskOutcome(
            status=task.status,
            updated_at=task.updated_at,
            result=run.result if run is not None else None,
            run_id=run.id if run is not None else None,
            approval_id=approval.id if approval is not None else None,
            approval_status=approval.status if approval is not None else None,
            error=error,
        )

    def events_since(self, context: TenantContext, event_type: str, after: datetime, limit: int) -> list[DomainEvent]:
        events = [
            event
            for event in self.repository.list_events(context)
            if event.type == event_type and event.workspace_id == context.workspace_id and event.created_at >= after
        ]
        events.sort(key=lambda event: (event.created_at, event.id))
        return events[:limit]

    def defer(self, job: DeferredStep) -> None:
        self.deferred.append(job)

    def flush(self) -> None:
        from .automation_dispatch import automation_dispatcher
        from .dependencies import event_runtime

        events, self.repository.recorded_events = self.repository.recorded_events, []
        event_runtime.after_commit(events)
        jobs, self.deferred = self.deferred, []
        if jobs:
            automation_dispatcher.submit_deferred(jobs)


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
    trigger: AutomationTrigger | None = None,
    trigger_payload: dict[str, Any] | None = None,
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
        trigger_id=trigger.id if trigger is not None else None,
        trigger_type=(trigger.event_type or trigger.kind.value) if trigger is not None else None,
        trigger_payload=dict(trigger_payload or {}),
        created_by=context.user_id,
        steps=[
            RunStepState(
                id=step.id,
                name=step.name,
                action=step.action,
                input=dict(step.input),
                status=RunStatus.QUEUED,
                max_attempts=step.max_attempts,
                retry_delay_seconds=step.retry_delay_seconds,
            )
            for step in workflow.steps
        ],
        created_at=now,
        updated_at=now,
    )


class StepFailed(Exception):
    """A step could not do its work; the run fails with this message (or retries)."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class _Wait:
    """Returned by ``_perform`` when the step continues after the transaction."""


WAIT = _Wait()


# -- templates: earlier results and the trigger's data in later steps ------------------------

_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+){0,6})\s*\}\}")


def template_scope(run: WorkflowRun) -> dict[str, Any]:
    """What ``{{...}}`` can name: ``trigger.*``, ``steps.<id>.*`` (finished steps), ``run.*``."""
    return {
        "trigger": run.trigger_payload,
        "steps": {step.id: step.output for step in run.steps if step.status == RunStatus.COMPLETED},
        "run": {"id": run.id, "trigger": run.trigger, "started_at": run.created_at.isoformat()},
        "workflow": {"id": run.workflow_id, "name": run.workflow_name or ""},
    }


def _lookup(scope: dict[str, Any], path: list[str]) -> Any:
    current: Any = scope
    for part in path:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def render_text(text: str, scope: dict[str, Any], *, untrusted: bool = False) -> str:
    """Replace ``{{path}}``; unknown paths become empty text.

    With ``untrusted`` (an agent task's prompt), values that came from outside (the
    trigger's data, earlier steps' results) are wrapped as labelled untrusted data, so a
    received email or webhook body is never read as the owner's instructions.
    """
    from .prompt_provenance import label_untrusted

    def value(match: re.Match[str]) -> str:
        path = match.group(1).split(".")
        found = _lookup(scope, path)
        if found is None:
            return ""
        text_value = found if isinstance(found, str) else json.dumps(found, ensure_ascii=False, default=str)
        text_value = text_value[:RESULT_MAX]
        if untrusted and path[0] in {"trigger", "steps"}:
            return label_untrusted(text_value, source=f"automation_{path[0]}", origin=".".join(path[:3]))
        return text_value

    return _TEMPLATE.sub(value, text)


def _input_text(state: RunStepState, key: str, scope: dict[str, Any], *, untrusted: bool = False) -> str:
    raw = state.input.get(key, "")
    return render_text(raw, scope, untrusted=untrusted).strip() if isinstance(raw, str) else ""


def email_arguments(state: RunStepState, run: WorkflowRun) -> dict[str, Any]:
    """The rendered ``email.send`` input, checked again (a template may render empty)."""
    scope = template_scope(run)
    raw_to = state.input.get("to")
    items = [raw_to] if isinstance(raw_to, str) else list(raw_to or [])
    recipients: list[str] = []
    for item in items:
        if isinstance(item, str):
            recipients.extend(part.strip() for part in render_text(item, scope).replace(";", ",").split(","))
    recipients = list(dict.fromkeys(item for item in recipients if item))[:EMAIL_RECIPIENTS_MAX]
    if not recipients:
        raise StepFailed('The email has no recipient (the "to" field rendered empty). Edit the step and retry the run.')
    body = _input_text(state, "body", scope)[:EMAIL_BODY_MAX]
    if not body:
        raise StepFailed("The email body rendered empty. Edit the step and retry the run.")
    return {
        "account_id": _input_text(state, "account_id", scope),
        "to": recipients,
        "subject": " ".join(_input_text(state, "subject", scope).split())[:EMAIL_SUBJECT_MAX],
        "body": body,
    }


def _deferred(run: WorkflowRun, state: RunStepState, context: TenantContext, **values: Any) -> DeferredStep:
    return DeferredStep(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor=run.created_by or context.user_id,
        run_id=run.id,
        workflow_id=run.workflow_id,
        step_id=state.id,
        attempt=state.attempt,
        action=state.action,
        **values,
    )


def _perform(
    state: RunStepState, run: WorkflowRun, context: TenantContext, effects: RunEffects, now: datetime
) -> dict[str, Any] | _Wait:
    scope = template_scope(run)
    if state.action == "task.create":
        title = " ".join(_input_text(state, "title", scope).split())[:TASK_TITLE_MAX]
        prompt = _input_text(state, "prompt", scope, untrusted=True)[:TASK_PROMPT_MAX]
        if not title or not prompt:
            raise StepFailed("The step's input has no task title or prompt. Edit the workflow and retry the run.")
        task = effects.create_task(context, title, prompt)
        state.output = {"task_id": task.id, "title": task.title}
        if state.input.get("run", True) is False:
            return {**state.output, "run": False}
        # The task runs after this transaction commits; the step waits for it.
        state.waiting_for = "task"
        run.waiting_task_id = task.id
        run.wake_at = now + timedelta(seconds=RECONCILE_SECONDS)
        effects.defer(_deferred(run, state, context, task_id=task.id))
        return WAIT
    if state.action == "email.send":
        arguments = email_arguments(state, run)
        state.output = {"account_id": arguments["account_id"], "to": arguments["to"], "subject": arguments["subject"]}
        state.waiting_for = "start"
        run.wake_at = now + timedelta(seconds=RECONCILE_SECONDS)
        effects.defer(_deferred(run, state, context, arguments=arguments))
        return WAIT
    if state.action == "notify":
        raw = state.input.get("message")
        message = render_text(raw, scope) if isinstance(raw, str) and raw.strip() else ""
        message = message or f"{run.workflow_name or 'An automation'} reached \"{state.name}\"."
        channels = [c for c in state.input.get("channels", ["in_app"]) if c in NOTIFY_CHANNELS] or ["in_app"]
        recipients = [run.created_by] if run.created_by and run.created_by != SCHEDULER_ACTOR else []
        effects.record_event(
            create_event(
                CanonicalEventName.AUTOMATION_NOTIFICATION,
                context,
                run.id,
                {
                    **_run_payload(run),
                    "message": message[:MESSAGE_MAX],
                    "notify_user_ids": recipients,
                    "channels": channels,
                    "push": "push" in channels,
                },
                correlation_id=run.id,
            ).event
        )
        return {"notified": recipients, "message": message[:MESSAGE_MAX], "channels": channels}
    if state.action == REPORT_STEP_ACTION:
        # A report of the workspace's data, shared with the workspace (docs/reports.md).
        from .reports import generate_for_automation

        try:
            return generate_for_automation(
                scheduler_context(run.tenant_id, run.workspace_id, run.created_by), state.input, run.id
            )
        except ValueError as exc:
            raise StepFailed(str(exc)) from exc
        except Exception as exc:
            raise StepFailed("The report could not be generated. Retry the run.") from exc
    # Older free-form actions are recorded as accepted; they have no executor.
    return {"accepted": True, "action": state.action}


def retry_delay(state: RunStepState) -> int:
    """Seconds before the next attempt: the step's delay doubled per failed attempt, capped."""
    return min(RETRY_DELAY_CAP_SECONDS, state.retry_delay_seconds * 2 ** max(0, state.attempt - 1))


def _step_failed(state: RunStepState, run: WorkflowRun, message: str, *, retryable: bool, now: datetime) -> None:
    state.error = message[: MESSAGE_MAX * 2]
    run.waiting_task_id = None
    if retryable and state.attempt < state.max_attempts:
        # Retried in place: the run stays running and wakes when the next attempt is due.
        state.status = RunStatus.RUNNING
        state.waiting_for = "retry"
        state.next_attempt_at = now + timedelta(seconds=retry_delay(state))
        run.status = RunStatus.RUNNING
        run.wake_at = state.next_attempt_at
        return
    state.status = RunStatus.FAILED
    state.waiting_for = None
    state.next_attempt_at = None
    run.status = RunStatus.FAILED
    run.wake_at = None


def _run_payload(run: WorkflowRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_name": run.workflow_name,
        "schedule_id": run.schedule_id,
        "trigger_id": run.trigger_id,
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
    now: datetime | None = None,
) -> WorkflowRun:
    """Run steps from ``current_step`` until the run pauses, waits, fails or completes."""
    now = now or utc_now()
    run.status = RunStatus.RUNNING
    run.wake_at = None
    while run.current_step < len(run.steps):
        state = run.steps[run.current_step]
        state.attempt += 1
        state.error = None
        state.waiting_for = None
        state.next_attempt_at = None
        state.started_at = now
        if state.action == "pause":
            state.status = RunStatus.PAUSED
            run.status = RunStatus.PAUSED
            break
        if state.action == "fail":
            state.status = RunStatus.FAILED
            state.error = render_text(str(state.input.get("message") or "Step failed"), template_scope(run))
            run.status = RunStatus.FAILED
            break
        state.status = RunStatus.RUNNING
        try:
            outcome = _perform(state, run, context, effects, now)
        except StepFailed as exc:
            _step_failed(state, run, str(exc), retryable=exc.retryable, now=now)
            break
        if isinstance(outcome, _Wait):
            break
        state.output = outcome
        state.status = RunStatus.COMPLETED
        run.current_step += 1
    if run.current_step == len(run.steps):
        run.status = RunStatus.COMPLETED
        run.wake_at = None
        run.waiting_task_id = None
    run.updated_at = now
    announce(run, context, effects, workflow_creator=workflow_creator)
    return run


def _complete_waiting(state: RunStepState, run: WorkflowRun, outcome: TaskOutcome) -> None:
    state.status = RunStatus.COMPLETED
    state.waiting_for = None
    state.output = {
        **state.output,
        "status": outcome.status.value,
        "result": (outcome.result or "")[:RESULT_MAX],
        **({"agent_run_id": outcome.run_id} if outcome.run_id else {}),
        **({"approval_id": outcome.approval_id} if outcome.approval_id else {}),
    }
    run.current_step += 1
    run.waiting_task_id = None


def _finish(run: WorkflowRun, context: TenantContext, effects: RunEffects, workflow_creator: str | None, now: datetime) -> WorkflowRun:
    run.updated_at = now
    announce(run, context, effects, workflow_creator=workflow_creator)
    return run


def advance_state(
    run: WorkflowRun,
    context: TenantContext,
    effects: RunEffects,
    *,
    report: StepReport | None = None,
    workflow_creator: str | None = None,
    now: datetime | None = None,
) -> WorkflowRun:
    """Move a running run whose current step waits: a due retry, a finished task, a report.

    Safe to call at any time (events, the dispatcher's report, the periodic pass): a step
    with nothing new keeps waiting and only its next check time moves.
    """
    now = now or utc_now()
    if run.status != RunStatus.RUNNING or run.current_step >= len(run.steps):
        return run
    state = run.steps[run.current_step]
    if state.waiting_for == "retry":
        if state.next_attempt_at is not None and state.next_attempt_at > now:
            run.wake_at = state.next_attempt_at
            return run
        return execute_run(run, context, effects, workflow_creator=workflow_creator, now=now)
    if state.waiting_for not in {"start", "task", "approval"}:
        return run
    if report is not None and report.step_id == state.id and report.attempt == state.attempt:
        if report.error:
            _step_failed(state, run, report.error, retryable=report.retryable, now=now)
            return _finish(run, context, effects, workflow_creator, now)
        state.output = {**state.output, **report.output}
        if report.task_id:
            state.output["task_id"] = report.task_id
            state.waiting_for = "task"
            run.waiting_task_id = report.task_id
    task_id = state.output.get("task_id") if state.waiting_for != "start" else None
    stale = state.started_at is None or (now - state.started_at).total_seconds() > STALE_DEFER_SECONDS
    if not task_id:
        # The work was queued but never reported (the process stopped): hand it out again.
        if stale:
            try:
                effects.defer(_deferred(run, state, context, arguments=email_arguments(state, run)))
            except StepFailed as exc:
                _step_failed(state, run, str(exc), retryable=False, now=now)
                return _finish(run, context, effects, workflow_creator, now)
            state.started_at = now
        run.wake_at = now + timedelta(seconds=RECONCILE_SECONDS)
        run.updated_at = now
        return run
    outcome = effects.task_outcome(context, str(task_id))
    if outcome is None:
        _step_failed(state, run, f"The agent task {task_id} no longer exists in this workspace.", retryable=False, now=now)
    elif outcome.status == TaskStatus.COMPLETED:
        _complete_waiting(state, run, outcome)
        return execute_run(run, context, effects, workflow_creator=workflow_creator, now=now)
    elif outcome.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        if outcome.approval_status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}:
            message = f"The approval for the task's action was {outcome.approval_status.value}, so the step stopped."
            _step_failed(state, run, message, retryable=False, now=now)
        elif outcome.status == TaskStatus.CANCELLED:
            _step_failed(state, run, "Someone cancelled the agent task this step started.", retryable=False, now=now)
        else:
            _step_failed(state, run, f"The agent task failed: {outcome.error or 'no reason given'}", retryable=True, now=now)
    elif outcome.status == TaskStatus.WAITING_APPROVAL:
        state.waiting_for = "approval"
        if outcome.approval_id:
            state.output = {**state.output, "approval_id": outcome.approval_id}
        run.wake_at = now + timedelta(seconds=RECONCILE_APPROVAL_SECONDS)
    else:
        state.waiting_for = "task"
        if outcome.status == TaskStatus.CREATED and state.action == "task.create" and stale:
            # Queued to run but never started (the process stopped): start it again.
            effects.defer(_deferred(run, state, context, task_id=str(task_id)))
            state.started_at = now
        run.wake_at = now + timedelta(seconds=RECONCILE_SECONDS)
    return _finish(run, context, effects, workflow_creator, now)


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
            step.waiting_for = None
            step.next_attempt_at = None
    run.wake_at = None
    run.waiting_task_id = None
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
        "not run. A workspace owner can take over the schedule (Take over) so it runs as them."
    )


def is_owner(context: TenantContext) -> bool:
    return any(role.strip().lower() == "owner" for role in context.roles)


def require_editor(context: TenantContext, created_by: str | None, kind: str) -> None:
    """Members change their own automations and schedules; owners change any."""
    if created_by and created_by != context.user_id and not is_owner(context):
        raise EditDenied(
            f"Only the {kind}'s creator ({created_by}) or a workspace owner can change this {kind}."
        )


def require_take_over(context: TenantContext, created_by: str | None, kind: str) -> None:
    """Only owners take over a schedule or trigger (it then runs as them)."""
    if not is_owner(context):
        raise EditDenied(
            f"Only a workspace owner can take over a {kind}. Ask an owner, or the {kind}'s creator, to change it."
        )
    if created_by == context.user_id:
        raise AutomationConflict(f"You already own this {kind}.")


# Triggers (engine-independent) ---------------------------------------------------------


def new_trigger(context: TenantContext, payload: TriggerCreate) -> tuple[AutomationTrigger, str | None]:
    """A new trigger and, for a webhook, its secret (stored encrypted, shown once)."""
    from .secret_box import secret_cipher

    now = utc_now()
    secret = new_secret() if payload.kind == TriggerKind.WEBHOOK else None
    trigger = AutomationTrigger(
        id=new_id("trigger"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_by=context.user_id,
        created_at=now,
        updated_at=now,
        cursor_at=now if payload.kind == TriggerKind.EVENT else None,
        secret_ciphertext=secret_cipher().encrypt(secret) if secret else None,
        secret_hint=secret_hint(secret) if secret else None,
        secret_rotated_at=now if secret else None,
        **payload.model_dump(),
    )
    return trigger, secret


def updated_trigger(trigger: AutomationTrigger, payload: TriggerUpdate) -> AutomationTrigger:
    updates = payload.model_dump(exclude_none=True, exclude={"conditions"})
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if not updates["name"]:
            raise ValueError("Give the trigger a name.")
    if "event_type" in updates:
        if trigger.kind != TriggerKind.EVENT:
            raise ValueError("A webhook trigger has no event type.")
        if updates["event_type"] not in TRIGGER_EVENTS:
            raise ValueError(f"Choose the event that starts the automation: one of {', '.join(TRIGGER_EVENTS)}.")
    now = utc_now()
    changed = trigger.model_copy(update={**updates, "updated_at": now})
    if payload.conditions is not None:
        changed.conditions = list(payload.conditions)
    if trigger.kind == TriggerKind.EVENT and (
        (payload.enabled and not trigger.enabled) or payload.event_type not in {None, trigger.event_type}
    ):
        # Turned on again or pointed at another event: only events from now on start runs.
        changed.cursor_at = now
    return changed


def rotated(trigger: AutomationTrigger) -> tuple[AutomationTrigger, str]:
    from .secret_box import secret_cipher

    if trigger.kind != TriggerKind.WEBHOOK:
        raise AutomationConflict("Only webhook triggers have a secret to rotate.")
    secret = new_secret()
    now = utc_now()
    return (
        trigger.model_copy(
            update={
                "secret_ciphertext": secret_cipher().encrypt(secret),
                "secret_hint": secret_hint(secret),
                "secret_rotated_at": now,
                "updated_at": now,
            }
        ),
        secret,
    )


def trigger_decision(
    trigger: AutomationTrigger,
    workflow: WorkflowDefinition | None,
    *,
    archived: bool,
    actor_ok: bool,
    recent: int,
    active: int,
    looped: bool,
) -> TriggerOutcome:
    """Whether a delivery to ``trigger`` starts a run now, and if not, why."""
    if not trigger.enabled:
        return TriggerOutcome.DISABLED
    if archived:
        return TriggerOutcome.WORKSPACE_ARCHIVED
    if looped:
        return TriggerOutcome.LOOP_SKIPPED
    if recent >= trigger.rate_limit_per_minute:
        return TriggerOutcome.RATE_LIMITED
    if workflow is None or workflow.status != WorkflowStatus.ACTIVE:
        return TriggerOutcome.WORKFLOW_PAUSED
    if not actor_ok:
        return TriggerOutcome.OWNER_INACTIVE
    if active and workflow.overlap_policy == OverlapPolicy.SKIP:
        return TriggerOutcome.SKIPPED_OVERLAP
    return TriggerOutcome.FIRED


def run_started_task(run: WorkflowRun, task_id: str) -> bool:
    return any(step.output.get("task_id") == task_id for step in run.steps)


def matching_triggers(triggers: list[AutomationTrigger], event: DomainEvent) -> list[AutomationTrigger]:
    payload = event_payload(event)
    return [
        trigger
        for trigger in triggers
        if trigger.kind == TriggerKind.EVENT
        and trigger.enabled
        and trigger.event_type == event.type
        and conditions_match(trigger.conditions, payload)
    ]


def run_actor(run: WorkflowRun) -> TenantContext:
    """The explicit actor a run's steps execute as: whoever started it (or its schedule's owner)."""
    return scheduler_context(run.tenant_id, run.workspace_id, run.created_by)


TRIGGER_RATE_WINDOW = timedelta(minutes=1)


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


# Feature step actions ------------------------------------------------------------------
#
# A feature can register a handler for its own action name (tyre planning registers
# ``tyre.report``, docs/tyre-planning.md#reports). The engine records such a step like any
# accepted step; the handler runs *after* the run is committed, outside every engine
# transaction, so it may call external systems without holding a row lock. A failure is
# logged and recorded by the feature itself and never undoes the run. A handler gets the
# run, the step's state and the step's input as the run recorded it.

StepHandler = Callable[[WorkflowRun, RunStepState, dict[str, Any]], None]
_step_handlers: dict[str, StepHandler] = {}


def register_step_handler(action: str, handler: StepHandler) -> None:
    _step_handlers[action] = handler


def dispatch_step_actions(
    runs: list[WorkflowRun],
    engine_provider: Callable[[], AutomationEngine] | None = None,
    *,
    since: datetime | None = None,
) -> int:
    """Run the registered handlers for the completed steps of ``runs``; returns how many ran.

    ``since``: only steps executed at or after that moment (a run woken from a waiting
    step must not repeat a handler that ran in an earlier pass).
    """
    handled = 0
    for run in runs:
        for step in run.steps:
            handler = _step_handlers.get(step.action)
            if handler is None or step.status != RunStatus.COMPLETED:
                continue
            if since is not None and (step.started_at is None or step.started_at < since):
                continue
            try:
                handler(run, step, dict(step.input))
                handled += 1
            except Exception:
                logger.exception("Automation run %s step %s (%s) failed", run.id, step.id, step.action)
    return handled


# Local SQLite engine -------------------------------------------------------------------

# Every statement is a fixed string per table: nothing is interpolated into SQL.
_SAVE = {
    "automation_workflows": "insert or replace into automation_workflows (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
    "automation_schedules": "insert or replace into automation_schedules (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
    "automation_triggers": "insert or replace into automation_triggers (id, tenant_id, workspace_id, body, created_at) values (?, ?, ?, ?, ?)",
}
_LIST = {
    "automation_workflows": "select body from automation_workflows where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
    "automation_schedules": "select body from automation_schedules where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
    "automation_runs": "select body from automation_runs where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
    "automation_triggers": "select body from automation_triggers where tenant_id = ? and workspace_id = ? order by created_at desc, id desc",
}
_DELETE = {
    "automation_workflows": "delete from automation_workflows where id = ? and tenant_id = ? and workspace_id = ?",
    "automation_schedules": "delete from automation_schedules where id = ? and tenant_id = ? and workspace_id = ?",
    "automation_triggers": "delete from automation_triggers where id = ? and tenant_id = ? and workspace_id = ?",
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
_ALL_RUNS = "select tenant_id, workspace_id, body from automation_runs"
_ALL_TRIGGERS = "select tenant_id, workspace_id, body from automation_triggers"
_DELETE_TRIGGERS_OF = (
    "delete from automation_triggers where tenant_id = ? and workspace_id = ? "
    "and json_extract(body, '$.workflow_id') = ?"
)


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
                create table if not exists automation_triggers (
                    id text primary key, tenant_id text not null, workspace_id text not null,
                    body text not null, created_at text not null
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
    def _put(connection: sqlite3.Connection, table: str, value: WorkflowDefinition | AutomationSchedule | AutomationTrigger) -> None:
        if isinstance(value, WorkflowDefinition):
            computed = set(SUMMARY_FIELDS)
        elif isinstance(value, AutomationTrigger):
            computed = {"webhook_path"}
        else:
            computed = {"cron_description", "cron_description_ar"}
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
            run.model_dump_json(exclude={"trigger", "error", "workflow_name", "waiting_for"}),
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
            connection.execute(_DELETE_TRIGGERS_OF, (*scope, workflow_id))
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
        trigger: AutomationTrigger | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> WorkflowRun | None:
        """Claim the idempotency key, apply the overlap policy and execute. None: key taken."""
        active = [r for r in self._runs(connection, context) if r.workflow_id == workflow.id and r.status in ACTIVE_RUN_STATUSES]
        run = new_run(
            context,
            workflow,
            idempotency_key=idempotency_key,
            retry_of=retry_of,
            schedule_id=schedule_id,
            trigger=trigger,
            trigger_payload=trigger_payload,
        )
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
            and (page.trigger_id is None or run.trigger_id == page.trigger_id)
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

    def take_over_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        with self._tx() as (connection, effects):
            schedule = self._schedule(connection, context, schedule_id)
            require_take_over(context, schedule.created_by, "schedule")
            previous = schedule.created_by
            updated = schedule.model_copy(update={"created_by": context.user_id, "updated_at": utc_now()})
            self._put(connection, "automation_schedules", updated)
            effects.record_audit(
                audit(context, "automation.schedule.taken_over", schedule_id, {"previous_owner": previous, "name": schedule.name})
            )
            return updated

    # -- waiting steps and retries ---------------------------------------------------------

    def _advance_in(
        self, connection: sqlite3.Connection, effects: RunEffects, run: WorkflowRun, report: StepReport | None, now: datetime
    ) -> WorkflowRun:
        context = run_actor(run)
        try:
            creator = self._workflow(connection, context, run.workflow_id).created_by
        except AutomationNotFound:
            creator = None
        before = run.model_dump_json()
        advance_state(run, context, effects, report=report, workflow_creator=creator, now=now)
        if run.model_dump_json() != before:
            connection.execute(_SAVE_RUN, self._run_values(run))
        return run

    def advance(self, context: TenantContext, run_id: str, report: StepReport | None = None) -> WorkflowRun:
        with self._tx() as (connection, effects):
            run = self._run(connection, context, run_id)
            return self._advance_in(connection, effects, run, report, utc_now())

    def wake_for_task(self, context: TenantContext, task_id: str) -> list[WorkflowRun]:
        with self._tx() as (connection, effects):
            waiting = [
                run for run in self._runs(connection, context) if run.status == RunStatus.RUNNING and run.waiting_task_id == task_id
            ]
            return [self._advance_in(connection, effects, run, None, utc_now()) for run in waiting]

    def run_due_wakeups(self, *, limit: int = 100, now: datetime | None = None) -> list[WorkflowRun]:
        now = now or utc_now()
        with self._tx() as (connection, effects):
            runs = [WorkflowRun.model_validate_json(row["body"]) for row in connection.execute(_ALL_RUNS).fetchall()]
            due = sorted(
                (run for run in runs if run.status == RunStatus.RUNNING and run.wake_at is not None and run.wake_at <= now),
                key=lambda run: run.wake_at or now,
            )[:limit]
            return [self._advance_in(connection, effects, run, None, now) for run in due]

    # -- triggers -------------------------------------------------------------------------

    def _trigger(self, connection: sqlite3.Connection, context: TenantContext, trigger_id: str) -> AutomationTrigger:
        trigger = next(
            (t for t in self._rows(connection, "automation_triggers", AutomationTrigger, context) if t.id == trigger_id), None
        )
        if trigger is None:
            raise AutomationNotFound("trigger", trigger_id)
        return trigger

    def create_trigger(self, context: TenantContext, payload: TriggerCreate) -> tuple[AutomationTrigger, str | None]:
        with self._tx() as (connection, effects):
            self._workflow(connection, context, payload.workflow_id)
            trigger, secret = new_trigger(context, payload)
            self._put(connection, "automation_triggers", trigger)
            effects.record_audit(
                audit(
                    context,
                    "automation.trigger.created",
                    trigger.id,
                    {"workflow_id": trigger.workflow_id, "kind": trigger.kind.value, "event_type": trigger.event_type},
                )
            )
            return trigger.public(), secret

    def list_triggers(self, context: TenantContext, workflow_id: str | None = None) -> list[AutomationTrigger]:
        with self._read() as connection:
            triggers = self._rows(connection, "automation_triggers", AutomationTrigger, context)
        return [t.public() for t in triggers if workflow_id is None or t.workflow_id == workflow_id]

    def get_trigger(self, context: TenantContext, trigger_id: str) -> AutomationTrigger:
        with self._read() as connection:
            return self._trigger(connection, context, trigger_id).public()

    def trigger_record(self, context: TenantContext, trigger_id: str) -> AutomationTrigger:
        with self._read() as connection:
            return self._trigger(connection, context, trigger_id)

    def update_trigger(self, context: TenantContext, trigger_id: str, payload: TriggerUpdate) -> AutomationTrigger:
        with self._tx() as (connection, effects):
            trigger = self._trigger(connection, context, trigger_id)
            require_editor(context, trigger.created_by, "trigger")
            changed = updated_trigger(trigger, payload)
            self._put(connection, "automation_triggers", changed)
            effects.record_audit(
                audit(context, "automation.trigger.updated", trigger_id, payload.model_dump(mode="json", exclude_none=True))
            )
            return changed.public()

    def delete_trigger(self, context: TenantContext, trigger_id: str) -> None:
        with self._tx() as (connection, effects):
            trigger = self._trigger(connection, context, trigger_id)
            require_editor(context, trigger.created_by, "trigger")
            connection.execute(_DELETE["automation_triggers"], (trigger_id, *self._scope(context)))
            effects.record_audit(audit(context, "automation.trigger.deleted", trigger_id, {"name": trigger.name}))

    def rotate_trigger_secret(self, context: TenantContext, trigger_id: str) -> tuple[AutomationTrigger, str]:
        with self._tx() as (connection, effects):
            trigger = self._trigger(connection, context, trigger_id)
            require_editor(context, trigger.created_by, "trigger")
            changed, secret = rotated(trigger)
            self._put(connection, "automation_triggers", changed)
            effects.record_audit(audit(context, "automation.trigger.secret_rotated", trigger_id, {"name": trigger.name}))
            return changed.public(), secret

    def take_over_trigger(self, context: TenantContext, trigger_id: str) -> AutomationTrigger:
        with self._tx() as (connection, effects):
            trigger = self._trigger(connection, context, trigger_id)
            require_take_over(context, trigger.created_by, "trigger")
            changed = trigger.model_copy(update={"created_by": context.user_id, "updated_at": utc_now()})
            self._put(connection, "automation_triggers", changed)
            effects.record_audit(
                audit(context, "automation.trigger.taken_over", trigger_id, {"previous_owner": trigger.created_by, "name": trigger.name})
            )
            return changed.public()

    def _fire_in(
        self,
        connection: sqlite3.Connection,
        effects: RunEffects,
        context: TenantContext,
        trigger: AutomationTrigger,
        *,
        key: str,
        payload: dict[str, Any],
        source_task_id: str | None,
        now: datetime,
    ) -> tuple[WorkflowRun | None, str]:
        existing = self._existing_by_key(connection, context, key)
        if existing is not None:
            return existing, "duplicate"
        actor = scheduler_context(context.tenant_id, context.workspace_id, trigger.created_by)
        try:
            workflow: WorkflowDefinition | None = self._workflow(connection, context, trigger.workflow_id)
        except AutomationNotFound:
            workflow = None
        runs = self._runs(connection, context)
        mine = [r for r in runs if r.workflow_id == trigger.workflow_id]
        outcome = trigger_decision(
            trigger,
            workflow,
            archived=_archived(context),
            actor_ok=effects.actor_may_run(actor, actor.user_id),
            recent=sum(1 for r in runs if r.trigger_id == trigger.id and r.created_at >= now - TRIGGER_RATE_WINDOW),
            active=sum(1 for r in mine if r.status in ACTIVE_RUN_STATUSES),
            looped=bool(source_task_id) and any(run_started_task(r, source_task_id or "") for r in mine),
        )
        run = None
        if outcome == TriggerOutcome.FIRED and workflow is not None:
            run = self._launch(connection, effects, actor, workflow, idempotency_key=key, trigger=trigger, trigger_payload=payload)
        changed = trigger.model_copy(
            update={
                "last_outcome": outcome.value,
                "last_fired_at": now if run is not None else trigger.last_fired_at,
                "fire_count": trigger.fire_count + (1 if run is not None else 0),
            }
        )
        self._put(connection, "automation_triggers", changed)
        return run, outcome.value

    def fire_trigger(
        self, context: TenantContext, trigger_id: str, *, key: str, payload: dict[str, Any], source_task_id: str | None = None
    ) -> tuple[WorkflowRun | None, str]:
        with self._tx() as (connection, effects):
            trigger = self._trigger(connection, context, trigger_id)
            return self._fire_in(
                connection, effects, context, trigger, key=key, payload=payload, source_task_id=source_task_id, now=utc_now()
            )

    def fire_event(self, context: TenantContext, event: DomainEvent) -> list[WorkflowRun]:
        fired: list[WorkflowRun] = []
        with self._tx() as (connection, effects):
            triggers = matching_triggers(self._rows(connection, "automation_triggers", AutomationTrigger, context), event)
            for trigger in triggers:
                run, outcome = self._fire_in(
                    connection,
                    effects,
                    context,
                    trigger,
                    key=event_fire_key(trigger.id, event.id),
                    payload=event_payload(event),
                    source_task_id=event_task_id(event),
                    now=utc_now(),
                )
                if run is not None and outcome != "duplicate":
                    fired.append(run)
        return fired

    def catch_up_triggers(self, *, limit: int = 100) -> list[WorkflowRun]:
        """Events a trigger has not seen yet (another process committed them, or this one stopped)."""
        fired: list[WorkflowRun] = []
        with self._tx() as (connection, effects):
            triggers = [
                AutomationTrigger.model_validate_json(row["body"]) for row in connection.execute(_ALL_TRIGGERS).fetchall()
            ]
            for trigger in triggers:
                if trigger.kind != TriggerKind.EVENT or not trigger.enabled or not trigger.event_type:
                    continue
                context = scheduler_context(trigger.tenant_id, trigger.workspace_id, None)
                cursor = trigger.cursor_at or trigger.created_at
                events = effects.events_since(context, trigger.event_type, cursor, limit)
                for event in events:
                    if not matching_triggers([trigger], event):
                        continue
                    run, outcome = self._fire_in(
                        connection,
                        effects,
                        context,
                        trigger,
                        key=event_fire_key(trigger.id, event.id),
                        payload=event_payload(event),
                        source_task_id=event_task_id(event),
                        now=utc_now(),
                    )
                    trigger = self._trigger(connection, context, trigger.id)
                    if run is not None and outcome != "duplicate":
                        fired.append(run)
                if events:
                    trigger = trigger.model_copy(update={"cursor_at": events[-1].created_at})
                    self._put(connection, "automation_triggers", trigger)
        return fired


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

# Other due work that runs on the same loop (one pass each, in the pass thread): the
# marketing publisher (docs/marketing.md#publishing). A failing pass is logged and does not
# stop the schedules or the other passes.
_SCHEDULER_PASSES: dict[str, Callable[[], int]] = {}


def register_scheduler_pass(name: str, run: Callable[[], int]) -> None:
    _SCHEDULER_PASSES[name] = run


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
        """One pass: due schedules, due retries and waiting checks, then event catch-up."""
        engine = self.engine_provider()
        started_at = utc_now()
        runs = engine.run_due_schedules(limit=self.batch_size)
        self.fired += len(runs)
        woken: list[WorkflowRun] = []
        for name, work in (("wakeups", engine.run_due_wakeups), ("triggers", engine.catch_up_triggers)):
            try:
                started = work(limit=self.batch_size)
            except Exception:
                logger.exception("Automation scheduler %s pass failed", name)
                continue
            if name == "triggers":
                self.fired += len(started)
                runs.extend(started)
            else:
                woken.extend(started)
        # After the fires are committed: feature step actions (for example tyre reports).
        dispatch_step_actions([*runs, *woken], self.engine_provider, since=started_at)
        for name, run in list(_SCHEDULER_PASSES.items()):
            try:
                run()
            except Exception:
                logger.exception("Scheduler pass %s failed", name)
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
            description_ar=cron.describe(payload.cron, "ar"),
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


@router.post("/schedules/{schedule_id}/take-over", response_model=AutomationSchedule)
def take_over_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationSchedule:
    """An owner takes over another member's schedule: from now on it runs as the owner (audited)."""
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().take_over_schedule(context, schedule_id)


# -- triggers ------------------------------------------------------------------------------


@router.get("/trigger-events", response_model=list[TriggerEvent])
def trigger_events(context: TenantContext = Depends(tenant_context)) -> list[TriggerEvent]:
    """The events an automation can start on, with the payload fields conditions can test."""
    _require(context, Permission.AUTOMATION_READ)
    return list(TRIGGER_EVENTS.values())


@router.get("/triggers", response_model=list[AutomationTrigger])
def list_triggers(
    response: Response,
    workflow_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[AutomationTrigger]:
    _require(context, Permission.AUTOMATION_READ)
    triggers = current_engine().list_triggers(context, workflow_id)
    response.headers[TOTAL_COUNT_HEADER] = str(len(triggers))
    return triggers[offset : offset + limit]


@router.post("/triggers", response_model=TriggerWithSecret, status_code=status.HTTP_201_CREATED)
def create_trigger(payload: TriggerCreate, context: TenantContext = Depends(tenant_context)) -> TriggerWithSecret:
    """Create a trigger. A webhook's secret is in this answer only; store it in the sender now."""
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        trigger, secret = current_engine().create_trigger(context, payload)
    return TriggerWithSecret(trigger=trigger, secret=secret)


@router.get("/triggers/{trigger_id}", response_model=AutomationTrigger)
def get_trigger(trigger_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationTrigger:
    _require(context, Permission.AUTOMATION_READ)
    with _handled():
        return current_engine().get_trigger(context, trigger_id)


@router.put("/triggers/{trigger_id}", response_model=AutomationTrigger)
def update_trigger(trigger_id: str, payload: TriggerUpdate, context: TenantContext = Depends(tenant_context)) -> AutomationTrigger:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().update_trigger(context, trigger_id, payload)


@router.post("/triggers/{trigger_id}/rotate-secret", response_model=TriggerWithSecret)
def rotate_trigger_secret(trigger_id: str, context: TenantContext = Depends(tenant_context)) -> TriggerWithSecret:
    """A new webhook secret; the old one stops working at once. Shown once."""
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        trigger, secret = current_engine().rotate_trigger_secret(context, trigger_id)
    return TriggerWithSecret(trigger=trigger, secret=secret)


@router.post("/triggers/{trigger_id}/take-over", response_model=AutomationTrigger)
def take_over_trigger(trigger_id: str, context: TenantContext = Depends(tenant_context)) -> AutomationTrigger:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        return current_engine().take_over_trigger(context, trigger_id)


@router.post("/triggers/{trigger_id}/{action}", response_model=AutomationTrigger)
def toggle_trigger(trigger_id: str, action: str, context: TenantContext = Depends(tenant_context)) -> AutomationTrigger:
    _require(context, Permission.AUTOMATION_MANAGE)
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=404, detail="Trigger action not found; use enable, disable, rotate-secret or take-over.")
    with _handled():
        return current_engine().update_trigger(context, trigger_id, TriggerUpdate(enabled=action == "enable"))


@router.delete("/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_trigger(trigger_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.AUTOMATION_MANAGE)
    with _handled():
        current_engine().delete_trigger(context, trigger_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_webhook_failures = InMemoryTokenBucket(rate_per_second=0.2, burst=20)
_HOOK_ID = re.compile(r"[A-Za-z0-9_\-]{1,80}")


def _log_safe(value: str) -> str:
    return value.replace("\r", "").replace("\n", "")[:80]


def _hook_refusal(code: int, kind: str, message: str, request: Request, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(status_code=code, content={"error": {"code": kind, "message": message}}, headers=headers)


@router.post("/hooks/{tenant_id}/{workspace_id}/{trigger_id}", response_model=WebhookAccepted, status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(tenant_id: str, workspace_id: str, trigger_id: str, request: Request) -> Any:
    """A signed delivery from an outside system starts the trigger's automation.

    No session: the HMAC signature with the trigger's secret is the credential. Refused:
    unsigned or wrongly signed (401), older than the replay window (401), over the
    trigger's rate limit (429), the trigger off or its automation paused (409).
    A delivery id already seen answers 200 with the run it started (idempotent).
    """
    # Path ids are plain identifiers; anything else is refused before it reaches a query or a log.
    if not all(_HOOK_ID.fullmatch(value) for value in (tenant_id, workspace_id, trigger_id)):
        return _hook_refusal(404, "not_found", "No webhook trigger at this address.", request)
    failures = _webhook_failures.acquire(client_key(request.scope))
    if not failures.allowed:
        return _hook_refusal(429, "rate_limited", "Too many refused deliveries; retry later.", request, failures.retry_after_seconds)
    body = await request.body()
    if len(body) > WEBHOOK_BODY_MAX_BYTES:
        return _hook_refusal(413, "too_large", f"Send at most {WEBHOOK_BODY_MAX_BYTES} bytes.", request)
    context = scheduler_context(tenant_id, workspace_id, None)
    engine = current_engine()
    try:
        trigger = await asyncio.to_thread(engine.trigger_record, context, trigger_id)
    except (AutomationNotFound, ScopeNotProvisionedError, KeyError):
        return _hook_refusal(404, "not_found", "No webhook trigger at this address.", request)
    if trigger.kind != TriggerKind.WEBHOOK or not trigger.secret_ciphertext:
        return _hook_refusal(404, "not_found", "No webhook trigger at this address.", request)
    from .secret_box import SecretDecryptionError, secret_cipher

    try:
        secret = secret_cipher().decrypt(trigger.secret_ciphertext)
        digest = verify(
            secret, request.headers.get(SIGNATURE_HEADER), body, now=int(utc_now().timestamp()), window_seconds=trigger.replay_window_seconds
        )
        delivery = delivery_id(request.headers.get(DELIVERY_HEADER), digest)
    except SecretDecryptionError:
        logger.warning("automation_webhook_secret_unreadable trigger=%s", _log_safe(trigger_id))
        return _hook_refusal(401, "unauthorized", "This trigger's secret cannot be read; rotate it.", request)
    except SignatureError as exc:
        _webhook_failures.acquire(client_key(request.scope))
        return _hook_refusal(401, "unauthorized", str(exc), request)
    if not trigger.enabled:
        return _hook_refusal(409, "disabled", "This trigger is turned off. Turn it on in Automations.", request)
    try:
        parsed: Any = json.loads(body) if body.strip() else {}
    except ValueError:
        parsed = {"text": body.decode("utf-8", "replace")[:4000]}
    payload = bounded({"body": parsed if isinstance(parsed, dict) else {"value": parsed}, "delivery_id": delivery, "received_at": utc_now().isoformat()})
    if not conditions_match(trigger.conditions, payload["body"] if isinstance(payload.get("body"), dict) else {}):
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "The delivery does not match the trigger's conditions."})
    try:
        run, outcome = await asyncio.to_thread(
            engine.fire_trigger, context, trigger_id, key=webhook_fire_key(trigger_id, delivery), payload=payload
        )
    except _HANDLED as exc:
        raise _http_error(exc) from exc
    if run is not None:
        duplicate = outcome == "duplicate"
        if not duplicate:
            await asyncio.to_thread(dispatch_step_actions, [run])
        return JSONResponse(
            status_code=200 if duplicate else 202,
            content=WebhookAccepted(run_id=run.id, status=run.status.value, duplicate=duplicate).model_dump(),
        )
    if outcome == TriggerOutcome.RATE_LIMITED.value:
        return _hook_refusal(429, "rate_limited", f"This trigger starts at most {trigger.rate_limit_per_minute} runs a minute.", request, 60)
    messages = {
        TriggerOutcome.WORKFLOW_PAUSED.value: "The automation is paused; resume it to accept deliveries.",
        TriggerOutcome.SKIPPED_OVERLAP.value: "The automation already has an active run and its overlap policy is skip.",
        TriggerOutcome.OWNER_INACTIVE.value: "The trigger's owner is no longer an active member; a workspace owner can take it over.",
        TriggerOutcome.WORKSPACE_ARCHIVED.value: "The workspace is archived.",
        TriggerOutcome.DISABLED.value: "This trigger is turned off.",
    }
    return _hook_refusal(409, outcome, messages.get(outcome, "The delivery did not start a run."), request)


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
        run = current_engine().start(context, workflow_id, idempotency_key)
    dispatch_step_actions([run])
    return run


@router.get("/runs", response_model=list[WorkflowRun])
def list_runs(
    response: Response,
    workflow_id: str | None = Query(default=None, max_length=80),
    schedule_id: str | None = Query(default=None, max_length=80),
    trigger_id: str | None = Query(default=None, max_length=80),
    run_status: RunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[WorkflowRun]:
    _require(context, Permission.AUTOMATION_READ)
    items, total = current_engine().page_runs(
        context, RunFilter(workflow_id=workflow_id, schedule_id=schedule_id, trigger_id=trigger_id, status=run_status, limit=limit, offset=offset)
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
        retried = selected.start(context, prior.workflow_id, idempotency_key, retry_of=prior.id)
    # A retry is a learning signal for the caller (docs/learning.md); best effort.
    from .learning import SignalKind, Surface
    from .learning_store import new_signal, record_server_signal

    record_server_signal(
        context,
        new_signal(SignalKind.RETRY, Surface.AUTOMATION, subject_id=prior.id, metadata={"workflow_id": prior.workflow_id}),
    )
    dispatch_step_actions([retried])
    return retried
