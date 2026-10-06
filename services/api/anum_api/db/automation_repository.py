"""PostgreSQL automation engine (migration 0009).

Each call is one transaction as the application role with the caller's tenant and
workspace RLS context set (``anum_api.maintenance.scoped_unit``); queries also filter on
both explicitly. Run transitions lock the run row (``FOR UPDATE``), so replicas cannot
cancel and resume the same run at once.

The scheduler discovers due schedules as ``anum_maintenance`` (ids only) and then, per
schedule and inside that schedule's tenant context, claims the row with
``FOR UPDATE SKIP LOCKED``, records the run and advances ``next_run_at`` in one
transaction. A replica that loses the race either skips the locked row or, after the
winner commits, no longer sees it as due. The unique ``(tenant, workspace,
idempotency_key)`` index is the last guard: one fire time, one run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from anum_api.automation import (
    AutomationSchedule,
    RunStepState,
    ScheduleCreate,
    ScheduleUpdate,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
    cancel_state,
    execute_run,
    new_run,
    next_fire,
    resume_state,
    schedule_fire_key,
    scheduler_context,
)
from anum_api.maintenance import SessionFactory, discover, scoped_unit
from anum_api.schemas import TenantContext, new_id, utc_now

from .models import AutomationRunRecord, AutomationScheduleRecord, AutomationWorkflowRecord
from .scope import require_workspace

logger = logging.getLogger(__name__)

# Runs as anum_maintenance: its policy shows only enabled schedules whose next_run_at has
# passed, and its grants cover these columns only.
_DUE_SCHEDULES = text(
    """
    select id, tenant_id, workspace_id
    from automation_schedules
    order by next_run_at, id
    limit :limit
    """
)
# Runs as the application role inside the schedule's tenant context.
_CLAIM_SCHEDULE = text(
    """
    select id, workflow_id, cron, timezone, next_run_at, created_by, now() as db_now
    from automation_schedules
    where id = :id and tenant_id = :tenant_id and workspace_id = :workspace_id
      and enabled and next_run_at is not null and next_run_at <= now()
    for update skip locked
    """
)


def _workflow(record: AutomationWorkflowRecord) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        steps=[WorkflowStep.model_validate(step) for step in record.steps],
        status=WorkflowStatus(record.status),
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _schedule(record: AutomationScheduleRecord) -> AutomationSchedule:
    return AutomationSchedule(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        workflow_id=record.workflow_id,
        name=record.name,
        cron=record.cron,
        timezone=record.timezone,
        enabled=record.enabled,
        next_run_at=record.next_run_at,
        last_run_at=record.last_run_at,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run(record: AutomationRunRecord) -> WorkflowRun:
    return WorkflowRun(
        id=record.id,
        workflow_id=record.workflow_id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        status=record.status,
        idempotency_key=record.idempotency_key,
        retry_of=record.retry_of,
        schedule_id=record.schedule_id,
        created_by=record.created_by,
        current_step=record.current_step,
        steps=[RunStepState.model_validate(step) for step in record.steps],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_values(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "tenant_id": run.tenant_id,
        "workspace_id": run.workspace_id,
        "workflow_id": run.workflow_id,
        "schedule_id": run.schedule_id,
        "status": run.status.value,
        "idempotency_key": run.idempotency_key,
        "retry_of": run.retry_of,
        "current_step": run.current_step,
        "steps": [step.model_dump(mode="json") for step in run.steps],
        "created_by": run.created_by or "unknown",
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


class PostgresAutomationEngine:
    def __init__(self, session_factory: SessionFactory, *, maintenance_session_factory: SessionFactory | None = None) -> None:
        self.session_factory = session_factory
        # Discovery needs a login that may SET ROLE anum_maintenance; by default the same.
        self.maintenance_session_factory = maintenance_session_factory or session_factory

    def _unit(self, context: TenantContext) -> Any:
        return scoped_unit(self.session_factory, context.tenant_id, context.workspace_id)

    # -- lookups (inside a unit) ------------------------------------------------------

    @staticmethod
    def _workflow_record(session: Session, context: TenantContext, workflow_id: str) -> AutomationWorkflowRecord:
        record = session.scalar(
            select(AutomationWorkflowRecord).where(
                AutomationWorkflowRecord.tenant_id == context.tenant_id,
                AutomationWorkflowRecord.workspace_id == context.workspace_id,
                AutomationWorkflowRecord.id == workflow_id,
            )
        )
        if record is None:
            raise KeyError(workflow_id)
        return record

    @staticmethod
    def _schedule_record(
        session: Session, context: TenantContext, schedule_id: str, *, lock: bool = False
    ) -> AutomationScheduleRecord:
        statement = select(AutomationScheduleRecord).where(
            AutomationScheduleRecord.tenant_id == context.tenant_id,
            AutomationScheduleRecord.workspace_id == context.workspace_id,
            AutomationScheduleRecord.id == schedule_id,
        )
        record = session.scalar(statement.with_for_update() if lock else statement)
        if record is None:
            raise KeyError(schedule_id)
        return record

    @staticmethod
    def _run_record(session: Session, context: TenantContext, run_id: str, *, lock: bool = False) -> AutomationRunRecord:
        statement = select(AutomationRunRecord).where(
            AutomationRunRecord.tenant_id == context.tenant_id,
            AutomationRunRecord.workspace_id == context.workspace_id,
            AutomationRunRecord.id == run_id,
        )
        record = session.scalar(statement.with_for_update() if lock else statement)
        if record is None:
            raise KeyError(run_id)
        return record

    @staticmethod
    def _run_by_key(session: Session, context: TenantContext, key: str) -> AutomationRunRecord | None:
        return session.scalar(
            select(AutomationRunRecord).where(
                AutomationRunRecord.tenant_id == context.tenant_id,
                AutomationRunRecord.workspace_id == context.workspace_id,
                AutomationRunRecord.idempotency_key == key,
            )
        )

    @staticmethod
    def _insert_run(session: Session, run: WorkflowRun) -> bool:
        """Insert the run; False when its idempotency key is already taken."""
        statement = pg_insert(AutomationRunRecord.__table__).values(**_run_values(run))
        if run.idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=["tenant_id", "workspace_id", "idempotency_key"],
                index_where=text("idempotency_key is not null"),
            )
        # RETURNING, not rowcount: an ORM-routed insert does not report a reliable count.
        inserted = session.connection().execute(statement.returning(AutomationRunRecord.__table__.c.id))
        return inserted.scalar_one_or_none() is not None

    @staticmethod
    def _save_run_state(record: AutomationRunRecord, run: WorkflowRun) -> None:
        record.status = run.status.value
        record.current_step = run.current_step
        record.steps = [step.model_dump(mode="json") for step in run.steps]
        record.updated_at = run.updated_at

    # -- workflows --------------------------------------------------------------------

    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition:
        now = utc_now()
        with self._unit(context) as session:
            require_workspace(session, context.tenant_id, context.workspace_id)
            record = AutomationWorkflowRecord(
                id=new_id("workflow"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                name=payload.name,
                description=payload.description,
                steps=[step.model_dump(mode="json") for step in payload.steps],
                status=WorkflowStatus.ACTIVE.value,
                version=1,
                created_by=context.user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return _workflow(record)

    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]:
        with self._unit(context) as session:
            records = session.scalars(
                select(AutomationWorkflowRecord)
                .where(
                    AutomationWorkflowRecord.tenant_id == context.tenant_id,
                    AutomationWorkflowRecord.workspace_id == context.workspace_id,
                )
                .order_by(AutomationWorkflowRecord.created_at.desc(), AutomationWorkflowRecord.id.desc())
            ).all()
            return [_workflow(record) for record in records]

    # -- schedules --------------------------------------------------------------------

    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule:
        now = utc_now()
        with self._unit(context) as session:
            self._workflow_record(session, context, payload.workflow_id)
            record = AutomationScheduleRecord(
                id=new_id("schedule"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                workflow_id=payload.workflow_id,
                name=payload.name,
                cron=payload.cron,
                timezone=payload.timezone,
                enabled=payload.enabled,
                next_run_at=next_fire(payload.cron, payload.timezone, now) if payload.enabled else None,
                created_by=context.user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return _schedule(record)

    def list_schedules(self, context: TenantContext) -> list[AutomationSchedule]:
        with self._unit(context) as session:
            records = session.scalars(
                select(AutomationScheduleRecord)
                .where(
                    AutomationScheduleRecord.tenant_id == context.tenant_id,
                    AutomationScheduleRecord.workspace_id == context.workspace_id,
                )
                .order_by(AutomationScheduleRecord.created_at.desc(), AutomationScheduleRecord.id.desc())
            ).all()
            return [_schedule(record) for record in records]

    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        with self._unit(context) as session:
            return _schedule(self._schedule_record(session, context, schedule_id))

    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule:
        now = utc_now()
        with self._unit(context) as session:
            record = self._schedule_record(session, context, schedule_id, lock=True)
            updates = payload.model_dump(exclude_none=True)
            was_enabled = record.enabled
            for field, value in updates.items():
                setattr(record, field, value)
            if record.enabled and (not was_enabled or {"cron", "timezone"} & updates.keys()):
                record.next_run_at = next_fire(record.cron, record.timezone, now)
            elif not record.enabled:
                record.next_run_at = None
            record.updated_at = now
            session.flush()
            return _schedule(record)

    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None:
        with self._unit(context) as session:
            self._schedule_record(session, context, schedule_id, lock=True)
            session.execute(
                delete(AutomationScheduleRecord).where(
                    AutomationScheduleRecord.tenant_id == context.tenant_id,
                    AutomationScheduleRecord.workspace_id == context.workspace_id,
                    AutomationScheduleRecord.id == schedule_id,
                )
            )

    # -- runs -------------------------------------------------------------------------

    def start(
        self,
        context: TenantContext,
        workflow_id: str,
        idempotency_key: str | None = None,
        retry_of: str | None = None,
    ) -> WorkflowRun:
        with self._unit(context) as session:
            if idempotency_key:
                existing = self._run_by_key(session, context, idempotency_key)
                if existing is not None:
                    return _run(existing)
            workflow = _workflow(self._workflow_record(session, context, workflow_id))
            run = execute_run(new_run(context, workflow, idempotency_key=idempotency_key, retry_of=retry_of), workflow)
            if not self._insert_run(session, run):
                # A concurrent request with the same key won the insert; return its run.
                winner = self._run_by_key(session, context, idempotency_key or "")
                if winner is None:  # pragma: no cover - the conflicting row must be visible
                    raise ValueError("Run with this idempotency key could not be read")
                return _run(winner)
            return run

    def list_runs(self, context: TenantContext) -> list[WorkflowRun]:
        with self._unit(context) as session:
            records = session.scalars(
                select(AutomationRunRecord)
                .where(
                    AutomationRunRecord.tenant_id == context.tenant_id,
                    AutomationRunRecord.workspace_id == context.workspace_id,
                )
                .order_by(AutomationRunRecord.created_at.desc(), AutomationRunRecord.id.desc())
            ).all()
            return [_run(record) for record in records]

    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._unit(context) as session:
            return _run(self._run_record(session, context, run_id))

    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._unit(context) as session:
            record = self._run_record(session, context, run_id, lock=True)
            run = cancel_state(_run(record))
            self._save_run_state(record, run)
            return run

    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._unit(context) as session:
            record = self._run_record(session, context, run_id, lock=True)
            workflow = _workflow(self._workflow_record(session, context, record.workflow_id))
            run = resume_state(_run(record), workflow)
            self._save_run_state(record, run)
            return run

    # -- scheduler --------------------------------------------------------------------

    def due_schedule_scopes(self, *, limit: int = 100) -> list[tuple[str, str, str]]:
        rows = discover(self.maintenance_session_factory, _DUE_SCHEDULES, {"limit": limit})
        return [(row.id, row.tenant_id, row.workspace_id) for row in rows]

    def fire_schedule(self, schedule_id: str, tenant_id: str, workspace_id: str) -> WorkflowRun | None:
        """Claim one due schedule in its tenant context; None if another replica has it."""
        with scoped_unit(self.session_factory, tenant_id, workspace_id) as session:
            claimed = session.execute(
                _CLAIM_SCHEDULE,
                {"id": schedule_id, "tenant_id": tenant_id, "workspace_id": workspace_id},
            ).mappings().one_or_none()
            if claimed is None:
                return None
            fire_at: datetime = claimed["next_run_at"]
            db_now: datetime = claimed["db_now"]
            context = scheduler_context(tenant_id, workspace_id, claimed["created_by"])
            run: WorkflowRun | None = None
            workflow_record = session.get(AutomationWorkflowRecord, claimed["workflow_id"])
            if workflow_record is not None and workflow_record.status == WorkflowStatus.ACTIVE.value:
                workflow = _workflow(workflow_record)
                run = new_run(
                    context,
                    workflow,
                    idempotency_key=schedule_fire_key(schedule_id, fire_at),
                    schedule_id=schedule_id,
                )
                execute_run(run, workflow)
                if not self._insert_run(session, run):
                    run = None  # this fire time already produced its run
            # A missed fire runs once; the schedule then continues from now, no catch-up.
            session.execute(
                text(
                    """
                    update automation_schedules
                    set next_run_at = :next_run_at, last_run_at = :last_run_at, updated_at = :updated_at
                    where id = :id and tenant_id = :tenant_id and workspace_id = :workspace_id
                    """
                ),
                {
                    "next_run_at": next_fire(claimed["cron"], claimed["timezone"], max(db_now, fire_at)),
                    "last_run_at": db_now,
                    "updated_at": db_now,
                    "id": schedule_id,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                },
            )
            return run

    def run_due_schedules(self, *, limit: int = 100) -> list[WorkflowRun]:
        runs: list[WorkflowRun] = []
        for schedule_id, tenant_id, workspace_id in self.due_schedule_scopes(limit=limit):
            try:
                run = self.fire_schedule(schedule_id, tenant_id, workspace_id)
            except Exception:
                # One broken schedule must not stop the others; it stays due and is retried.
                logger.exception("Automation schedule %s could not be fired", schedule_id)
                continue
            if run is not None:
                runs.append(run)
        return runs
