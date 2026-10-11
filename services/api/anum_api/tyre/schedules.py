"""Scheduled tyre reports through the automation engine (docs/automation.md).

A schedule is an ordinary automation workflow with one ``tyre.report`` step and an
automation schedule (cron and time zone), so it shows in the Workbench like any other.
When it fires, the automation engine commits the run and then calls
:func:`run_report_step` outside its transaction, which:

1. re-checks that the schedule's creator is still an active member who may operate tyre
   planning, and acts as that person;
2. syncs the source (if the sync fails, the report uses the last synced data and says so);
3. generates the report once per run step (``automation:<run id>:<step id>``); and
4. when the schedule names a channel and recipient, asks for delivery through the generic
   delivery path, which waits for an approval like any external send.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import automation
from ..authorization import Permission
from ..dependencies import _unit_of_work, has_permission, lookup_membership, tenant_context
from ..schemas import TenantContext
from ..scoped_store import ScopeNotProvisionedError
from . import service
from .reports import TITLES
from .sources import TyreSourceError
from .store import ReportKind, ReportSource, open_tyre_store

logger = logging.getLogger("anum.tyre.schedules")

STEP_ACTION = "tyre.report"
WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")

router = APIRouter()


class ScheduleDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern=r"^[a-z_]{2,30}$")
    to: str = Field(min_length=3, max_length=200)
    format: Literal["pdf", "csv"] = "pdf"


class ReportScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReportKind
    cadence: Literal["daily", "weekly", "monthly"]
    weekday: int = Field(default=0, ge=0, le=6, description="0 = Sunday (weekly only)")
    time: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Riyadh", min_length=1, max_length=80)
    delivery: ScheduleDelivery | None = None

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        from .. import cron

        try:
            cron.zone(value)
        except cron.CronError as exc:
            raise ValueError(str(exc)) from None
        return value

    @model_validator(mode="after")
    def _recipient(self) -> ReportScheduleCreate:
        if self.delivery is not None:
            from .api import DeliveryInput

            DeliveryInput(channel=self.delivery.channel, to=self.delivery.to, format=self.delivery.format)
        return self

    def cron_expression(self) -> str:
        hour, minute = (int(part) for part in self.time.split(":"))
        if self.cadence == "daily":
            return f"{minute} {hour} * * *"
        if self.cadence == "weekly":
            return f"{minute} {hour} * * {self.weekday}"
        return f"{minute} {hour} 1 * *"

    def describe(self) -> str:
        if self.cadence == "daily":
            return f"Daily at {self.time}"
        if self.cadence == "weekly":
            return f"Every {WEEKDAYS[self.weekday].capitalize()} at {self.time}"
        return f"Monthly on the 1st at {self.time}"


class ReportScheduleView(BaseModel):
    id: str
    workflow_id: str
    kind: ReportKind
    title: str
    cadence: str
    description: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_by: str | None
    delivery: dict[str, Any] | None


def _tyre_step(workflow: automation.WorkflowDefinition) -> automation.WorkflowStep | None:
    return next((step for step in workflow.steps if step.action == STEP_ACTION), None)


def list_report_schedules(context: TenantContext) -> list[ReportScheduleView]:
    engine = automation.current_engine()
    workflows = {workflow.id: workflow for workflow in engine.list_workflows(context)}
    views = []
    for schedule in engine.list_schedules(context):
        workflow = workflows.get(schedule.workflow_id)
        step = _tyre_step(workflow) if workflow else None
        if step is None:
            continue
        kind = ReportKind(step.input.get("kind", ReportKind.FLEET_HEALTH.value))
        views.append(
            ReportScheduleView(
                id=schedule.id, workflow_id=schedule.workflow_id, kind=kind, title=TITLES[kind],
                cadence=str(step.input.get("cadence", "")), description=str(step.input.get("description", "")),
                cron=schedule.cron, timezone=schedule.timezone, enabled=schedule.enabled,
                next_run_at=schedule.next_run_at, last_run_at=schedule.last_run_at,
                created_by=schedule.created_by, delivery=step.input.get("delivery"),
            )
        )
    return views


def _require_operator(context: TenantContext) -> None:
    from .api import OPERATORS_ONLY

    if not has_permission(context, Permission.TYRE_OPERATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OPERATORS_ONLY)


@router.get("/report-schedules", response_model=list[ReportScheduleView])
def get_report_schedules(context: TenantContext = Depends(tenant_context)) -> list[ReportScheduleView]:
    if not has_permission(context, Permission.TYRE_READ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot read tyre planning.")
    return list_report_schedules(context)


@router.post("/report-schedules", response_model=ReportScheduleView, status_code=status.HTTP_201_CREATED)
def create_report_schedule(
    payload: ReportScheduleCreate, context: TenantContext = Depends(tenant_context)
) -> ReportScheduleView:
    _require_operator(context)
    engine = automation.current_engine()
    title = TITLES[payload.kind]
    step_input: dict[str, Any] = {
        "kind": payload.kind.value,
        "cadence": payload.cadence,
        "description": payload.describe(),
        "delivery": payload.delivery.model_dump() if payload.delivery else None,
    }
    try:
        workflow = engine.create_workflow(
            context,
            automation.WorkflowCreate(
                name=f"Tyre report: {title} ({payload.cadence})",
                description="Created from Tyre planning > Reports. Syncs the tyre data and generates the report.",
                steps=[automation.WorkflowStep(id="report", name=title, action=STEP_ACTION, input=step_input,
                                               max_attempts=1)],
            ),
        )
        schedule = engine.create_schedule(
            context,
            automation.ScheduleCreate(workflow_id=workflow.id, name=f"{title} - {payload.describe()}",
                                      cron=payload.cron_expression(), timezone=payload.timezone),
        )
    except ScopeNotProvisionedError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Complete onboarding for this workspace first") from None
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return next(view for view in list_report_schedules(context) if view.id == schedule.id)


@router.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_report_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require_operator(context)
    if not any(view.id == schedule_id for view in list_report_schedules(context)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report schedule not found.")
    automation.current_engine().delete_schedule(context, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the step action ------------------------------------------------------------------------


def acting_context(tenant_id: str, workspace_id: str, user_id: str | None) -> TenantContext | None:
    """The schedule creator, with their current role, if they may still operate tyre planning."""
    if not user_id or user_id.startswith("system:"):
        return None
    probe = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=[])
    membership = lookup_membership(probe)
    if membership is None or not getattr(membership, "active", False):
        return None
    role = getattr(membership.role, "value", membership.role)
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=[str(role)])
    return context if has_permission(context, Permission.TYRE_OPERATE) else None


def run_report_step(run: automation.WorkflowRun, step: automation.RunStepState, step_input: dict[str, Any]) -> None:
    context = acting_context(run.tenant_id, run.workspace_id, run.created_by)
    if context is None:
        logger.warning("Tyre report schedule %s skipped: its creator may no longer operate tyre planning",
                       run.schedule_id)
        return
    asyncio.run(_run_report(context, run, step, step_input))


async def _run_report(context: TenantContext, run: automation.WorkflowRun, step: automation.RunStepState,
                      step_input: dict[str, Any]) -> None:
    key = f"automation:{run.id}:{step.id}"
    with open_tyre_store(context) as store:
        if store.find_report(context, key) is not None:
            return  # this fire already produced its report
    sync_problem = None
    try:
        await service.sync(context, context.user_id)
    except (TyreSourceError, service.NotConnectedError) as exc:
        sync_problem = getattr(exc, "message", str(exc))
    try:
        report = service.generate_report(context, ReportKind(step_input.get("kind", "fleet_health")),
                                         source=ReportSource.SCHEDULE, schedule_id=run.schedule_id,
                                         idempotency_key=key)
    except (service.NotConnectedError, service.NotSyncedError) as exc:
        logger.warning("Tyre report schedule %s produced no report: %s", run.schedule_id, exc)
        return
    if sync_problem:
        report.content.setdefault("notes", []).insert(
            0, f"The scheduled sync failed ({sync_problem}); this report uses the data synced at {report.data_as_of}."
        )
        with open_tyre_store(context) as store:
            store.save_report(report)
    target = step_input.get("delivery")
    if not target:
        return
    from .api import DeliveryInput, deliver

    with _unit_of_work(context, require_membership=False) as repository:
        try:
            await deliver(context, repository, report, DeliveryInput(**target))
        except HTTPException as exc:
            # Recorded on the report so the Reports page says why nothing was sent.
            logger.warning("Tyre report %s was not delivered (HTTP %s)", report.id, exc.status_code)
            with open_tyre_store(context) as store:
                current = store.get_report(context, report.id)
                if current is not None:
                    current.deliveries.append({
                        "channel": target.get("channel"), "to": target.get("to"), "format": target.get("format"),
                        "status": "unavailable", "approval_id": None, "task_id": None,
                        "requested_at": report.created_at.isoformat(), "requested_by": context.user_id,
                        "message": str(exc.detail)[:300],
                    })
                    store.save_report(current)


automation.register_step_handler(STEP_ACTION, run_report_step)
