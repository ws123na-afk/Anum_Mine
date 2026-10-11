"""PostgreSQL automation engine (migrations 0011 and 0031).

Each call is one transaction as the application role with the caller's tenant and
workspace RLS context set (``anum_api.maintenance.scoped_unit``); queries also filter on
both explicitly. Step effects (agent tasks, events, audit records) are written through the
same session, so a run and what it did commit or roll back together; events are handed to
the event bus only after the commit.

Run transitions lock the run row (``FOR UPDATE``), so replicas cannot cancel and resume the
same run at once. Starting a run locks its workflow row first, so the overlap policy sees
every active run even when replicas start the same workflow at the same moment.

The scheduler discovers due schedules as ``anum_maintenance`` (ids only) and then, per
schedule and inside that schedule's tenant context, claims the row with
``FOR UPDATE SKIP LOCKED``, records the run and advances ``next_run_at`` in one
transaction. A replica that loses the race either skips the locked row or, after the
winner commits, no longer sees it as due. The unique ``(tenant, workspace,
idempotency_key)`` index is the last guard: one fire time, one run. The run row is inserted
before any step runs, so a fire time that already has its run does no work twice.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from anum_api.automation import (
    ACTIVE_RUN_STATUSES,
    SUMMARY_FIELDS,
    AutomationConflict,
    AutomationNotFound,
    AutomationSchedule,
    OverlapPolicy,
    RepositoryEffects,
    RunFilter,
    RunStatus,
    RunStepState,
    ScheduleCreate,
    ScheduleOutcome,
    ScheduleUpdate,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowFilter,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
    WorkflowUpdate,
    announce,
    audit,
    cancel_state,
    execute_run,
    fail_before_start,
    new_run,
    next_fire,
    overlap_check,
    owner_inactive_message,
    require_editor,
    resume_state,
    schedule_fire_key,
    scheduler_context,
)
from anum_api import cron
from anum_api.maintenance import SessionFactory, discover, scoped_unit
from anum_api.schemas import TenantContext, new_id, utc_now

from .models import AutomationRunRecord, AutomationScheduleRecord, AutomationWorkflowRecord
from .scope import require_workspace

logger = logging.getLogger(__name__)

W, S, R = AutomationWorkflowRecord, AutomationScheduleRecord, AutomationRunRecord
_ACTIVE = [status.value for status in ACTIVE_RUN_STATUSES]

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
_ADVANCE_SCHEDULE = text(
    """
    update automation_schedules
    set next_run_at = :next_run_at, last_run_at = :last_run_at, last_outcome = :last_outcome,
        updated_at = :updated_at
    where id = :id and tenant_id = :tenant_id and workspace_id = :workspace_id
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
        overlap_policy=OverlapPolicy(record.overlap_policy or OverlapPolicy.ALLOW.value),
        status=WorkflowStatus(record.status),
        version=record.version,
        created_by=record.created_by,
        updated_by=record.updated_by,
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
        last_outcome=record.last_outcome,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run(record: AutomationRunRecord, workflow_name: str | None = None) -> WorkflowRun:
    return WorkflowRun(
        id=record.id,
        workflow_id=record.workflow_id,
        workflow_name=workflow_name,
        workflow_version=record.workflow_version,
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
        "workflow_version": run.workflow_version,
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


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def session_effects(session: Session, context: TenantContext) -> RepositoryEffects:
    """Effects written through the engine's own session (same transaction)."""
    from .repository import SqlAlchemyRepository
    from .workspace_settings_repository import SqlAlchemyNotificationPreferenceStore

    preferences = SqlAlchemyNotificationPreferenceStore(session)
    return RepositoryEffects(SqlAlchemyRepository(session, created_by_user_id=context.user_id), preferences.get)


class PostgresAutomationEngine:
    def __init__(self, session_factory: SessionFactory, *, maintenance_session_factory: SessionFactory | None = None) -> None:
        self.session_factory = session_factory
        # Discovery needs a login that may SET ROLE anum_maintenance; by default the same.
        self.maintenance_session_factory = maintenance_session_factory or session_factory

    def _unit(self, context: TenantContext) -> Any:
        return scoped_unit(self.session_factory, context.tenant_id, context.workspace_id)

    @contextmanager
    def _work(self, context: TenantContext) -> Iterator[tuple[Session, RepositoryEffects]]:
        """One transaction with effects; its events reach the bus only after commit."""
        with self._unit(context) as session:
            effects = session_effects(session, context)
            yield session, effects
        effects.flush()

    # -- lookups (inside a unit) ------------------------------------------------------

    @staticmethod
    def _scoped(statement: Select[Any], model: Any, context: TenantContext) -> Select[Any]:
        return statement.where(model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id)

    def _workflow_record(
        self, session: Session, context: TenantContext, workflow_id: str, *, lock: bool = False
    ) -> AutomationWorkflowRecord:
        statement = self._scoped(select(W), W, context).where(W.id == workflow_id)
        record = session.scalar(statement.with_for_update() if lock else statement)
        if record is None:
            raise AutomationNotFound("automation", workflow_id)
        return record

    def _schedule_record(
        self, session: Session, context: TenantContext, schedule_id: str, *, lock: bool = False
    ) -> AutomationScheduleRecord:
        statement = self._scoped(select(S), S, context).where(S.id == schedule_id)
        record = session.scalar(statement.with_for_update() if lock else statement)
        if record is None:
            raise AutomationNotFound("schedule", schedule_id)
        return record

    def _run_record(self, session: Session, context: TenantContext, run_id: str, *, lock: bool = False) -> AutomationRunRecord:
        statement = self._scoped(select(R), R, context).where(R.id == run_id)
        record = session.scalar(statement.with_for_update() if lock else statement)
        if record is None:
            raise AutomationNotFound("run", run_id)
        return record

    def _run_by_key(self, session: Session, context: TenantContext, key: str) -> AutomationRunRecord | None:
        return session.scalar(self._scoped(select(R), R, context).where(R.idempotency_key == key))

    def _active_records(self, session: Session, context: TenantContext, workflow_id: str) -> list[AutomationRunRecord]:
        return list(
            session.scalars(
                self._scoped(select(R), R, context)
                .where(R.workflow_id == workflow_id, R.status.in_(_ACTIVE))
                .order_by(R.created_at, R.id)
                .with_for_update()
            ).all()
        )

    @staticmethod
    def _insert_run(session: Session, run: WorkflowRun) -> bool:
        """Insert the run; False when its idempotency key is already taken."""
        statement = pg_insert(R.__table__).values(**_run_values(run))
        if run.idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=["tenant_id", "workspace_id", "idempotency_key"],
                index_where=text("idempotency_key is not null"),
            )
        # RETURNING, not rowcount: an ORM-routed insert does not report a reliable count.
        inserted = session.connection().execute(statement.returning(R.__table__.c.id))
        return inserted.scalar_one_or_none() is not None

    @staticmethod
    def _save_run_state(record: AutomationRunRecord, run: WorkflowRun) -> None:
        record.status = run.status.value
        record.current_step = run.current_step
        record.steps = [step.model_dump(mode="json") for step in run.steps]
        record.updated_at = run.updated_at

    def _summaries(self, session: Session, context: TenantContext, workflows: list[WorkflowDefinition]) -> list[WorkflowDefinition]:
        ids = [workflow.id for workflow in workflows]
        if not ids:
            return []
        stats = {
            row.workflow_id: row
            for row in session.execute(
                self._scoped(
                    select(
                        R.workflow_id,
                        func.count().label("runs"),
                        func.count().filter(R.status.in_(_ACTIVE)).label("active"),
                        func.max(R.created_at).label("last_at"),
                    ),
                    R,
                    context,
                )
                .where(R.workflow_id.in_(ids))
                .group_by(R.workflow_id)
            )
        }
        last_status = {
            row.workflow_id: row.status
            for row in session.execute(
                self._scoped(select(R.workflow_id, R.status), R, context)
                .where(R.workflow_id.in_(ids))
                .distinct(R.workflow_id)
                .order_by(R.workflow_id, R.created_at.desc(), R.id.desc())
            )
        }
        timing = {
            row.workflow_id: row
            for row in session.execute(
                self._scoped(
                    select(
                        S.workflow_id,
                        func.count().label("schedules"),
                        func.min(S.next_run_at).filter(S.enabled).label("next_at"),
                    ),
                    S,
                    context,
                )
                .where(S.workflow_id.in_(ids))
                .group_by(S.workflow_id)
            )
        }
        result = []
        for workflow in workflows:
            run_row, schedule_row = stats.get(workflow.id), timing.get(workflow.id)
            result.append(
                workflow.model_copy(
                    update={
                        "run_count": run_row.runs if run_row else 0,
                        "active_runs": run_row.active if run_row else 0,
                        "last_run_at": run_row.last_at if run_row else None,
                        "last_run_status": RunStatus(last_status[workflow.id]) if workflow.id in last_status else None,
                        "schedule_count": schedule_row.schedules if schedule_row else 0,
                        "next_run_at": schedule_row.next_at
                        if schedule_row and workflow.status == WorkflowStatus.ACTIVE
                        else None,
                    }
                )
            )
        return result

    # -- workflows --------------------------------------------------------------------

    def create_workflow(self, context: TenantContext, payload: WorkflowCreate) -> WorkflowDefinition:
        now = utc_now()
        with self._work(context) as (session, effects):
            require_workspace(session, context.tenant_id, context.workspace_id)
            record = W(
                id=new_id("workflow"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                name=payload.name,
                description=payload.description,
                steps=[step.model_dump(mode="json") for step in payload.steps],
                overlap_policy=payload.overlap_policy.value,
                status=WorkflowStatus.ACTIVE.value,
                version=1,
                created_by=context.user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            effects.record_audit(audit(context, "automation.workflow.created", record.id, {"name": record.name}))
            return _workflow(record)

    def list_workflows(self, context: TenantContext) -> list[WorkflowDefinition]:
        return self.page_workflows(context, WorkflowFilter(limit=10_000))[0]

    def page_workflows(self, context: TenantContext, page: WorkflowFilter) -> tuple[list[WorkflowDefinition], int]:
        with self._unit(context) as session:
            statement = self._scoped(select(W), W, context)
            if page.status is not None:
                statement = statement.where(W.status == page.status.value)
            query = (page.query or "").strip()
            if query:
                pattern = _like(query)
                statement = statement.where(or_(W.name.ilike(pattern, escape="\\"), W.description.ilike(pattern, escape="\\")))
            total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
            records = session.scalars(
                statement.order_by(W.created_at.desc(), W.id.desc()).offset(page.offset).limit(page.limit)
            ).all()
            return self._summaries(session, context, [_workflow(record) for record in records]), int(total)

    def get_workflow(self, context: TenantContext, workflow_id: str) -> WorkflowDefinition:
        with self._unit(context) as session:
            workflow = _workflow(self._workflow_record(session, context, workflow_id))
            return self._summaries(session, context, [workflow])[0]

    def update_workflow(self, context: TenantContext, workflow_id: str, payload: WorkflowUpdate) -> WorkflowDefinition:
        with self._work(context) as (session, effects):
            record = self._workflow_record(session, context, workflow_id, lock=True)
            require_editor(context, record.created_by, "automation")
            record.name = payload.name
            record.description = payload.description
            record.steps = [step.model_dump(mode="json") for step in payload.steps]
            record.overlap_policy = payload.overlap_policy.value
            record.version = record.version + 1
            record.updated_by = context.user_id
            record.updated_at = utc_now()
            session.flush()
            effects.record_audit(
                audit(context, "automation.workflow.updated", workflow_id, {"name": record.name, "version": record.version})
            )
            return self._summaries(session, context, [_workflow(record)])[0]

    def set_workflow_status(self, context: TenantContext, workflow_id: str, status: WorkflowStatus) -> WorkflowDefinition:
        with self._work(context) as (session, effects):
            record = self._workflow_record(session, context, workflow_id, lock=True)
            require_editor(context, record.created_by, "automation")
            if record.status != status.value:
                record.status = status.value
                record.updated_by = context.user_id
                record.updated_at = utc_now()
                session.flush()
                action = "automation.workflow.resumed" if status == WorkflowStatus.ACTIVE else "automation.workflow.paused"
                effects.record_audit(audit(context, action, workflow_id, {"name": record.name}))
            return self._summaries(session, context, [_workflow(record)])[0]

    def delete_workflow(self, context: TenantContext, workflow_id: str) -> None:
        with self._work(context) as (session, effects):
            record = self._workflow_record(session, context, workflow_id, lock=True)
            require_editor(context, record.created_by, "automation")
            active = self._active_records(session, context, workflow_id)
            if active:
                raise AutomationConflict(
                    f'"{record.name}" has {len(active)} active run(s). Cancel them first, then delete the automation.'
                )
            scope = and_(R.tenant_id == context.tenant_id, R.workspace_id == context.workspace_id, R.workflow_id == workflow_id)
            runs_deleted = session.execute(delete(R).where(scope)).rowcount
            session.execute(
                delete(S).where(S.tenant_id == context.tenant_id, S.workspace_id == context.workspace_id, S.workflow_id == workflow_id)
            )
            session.execute(
                delete(W).where(W.tenant_id == context.tenant_id, W.workspace_id == context.workspace_id, W.id == workflow_id)
            )
            effects.record_audit(
                audit(context, "automation.workflow.deleted", workflow_id, {"name": record.name, "runs_deleted": runs_deleted})
            )

    # -- schedules --------------------------------------------------------------------

    def create_schedule(self, context: TenantContext, payload: ScheduleCreate) -> AutomationSchedule:
        now = utc_now()
        with self._work(context) as (session, effects):
            self._workflow_record(session, context, payload.workflow_id)
            record = S(
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
            effects.record_audit(
                audit(context, "automation.schedule.created", record.id, {"workflow_id": record.workflow_id, "cron": record.cron})
            )
            return _schedule(record)

    def list_schedules(self, context: TenantContext, workflow_id: str | None = None) -> list[AutomationSchedule]:
        with self._unit(context) as session:
            statement = self._scoped(select(S), S, context)
            if workflow_id is not None:
                statement = statement.where(S.workflow_id == workflow_id)
            records = session.scalars(statement.order_by(S.created_at.desc(), S.id.desc())).all()
            return [_schedule(record) for record in records]

    def get_schedule(self, context: TenantContext, schedule_id: str) -> AutomationSchedule:
        with self._unit(context) as session:
            return _schedule(self._schedule_record(session, context, schedule_id))

    def update_schedule(self, context: TenantContext, schedule_id: str, payload: ScheduleUpdate) -> AutomationSchedule:
        now = utc_now()
        with self._work(context) as (session, effects):
            record = self._schedule_record(session, context, schedule_id, lock=True)
            require_editor(context, record.created_by, "schedule")
            updates = payload.model_dump(exclude_none=True)
            was_enabled = record.enabled
            for field, value in updates.items():
                setattr(record, field, value)
            cron.validate(record.cron, record.timezone)
            if record.enabled and (not was_enabled or {"cron", "timezone"} & updates.keys()):
                record.next_run_at = next_fire(record.cron, record.timezone, now)
            elif not record.enabled:
                record.next_run_at = None
            record.updated_at = now
            session.flush()
            effects.record_audit(audit(context, "automation.schedule.updated", schedule_id, dict(updates)))
            return _schedule(record)

    def delete_schedule(self, context: TenantContext, schedule_id: str) -> None:
        with self._work(context) as (session, effects):
            record = self._schedule_record(session, context, schedule_id, lock=True)
            require_editor(context, record.created_by, "schedule")
            name = record.name
            session.execute(
                delete(S).where(S.tenant_id == context.tenant_id, S.workspace_id == context.workspace_id, S.id == schedule_id)
            )
            effects.record_audit(audit(context, "automation.schedule.deleted", schedule_id, {"name": name}))

    # -- runs -------------------------------------------------------------------------

    def _launch(
        self,
        session: Session,
        effects: RepositoryEffects,
        context: TenantContext,
        workflow: WorkflowDefinition,
        *,
        idempotency_key: str | None,
        retry_of: str | None = None,
        schedule_id: str | None = None,
    ) -> WorkflowRun | None:
        """Claim the key, apply the overlap policy, execute. None: the key is already taken."""
        active = self._active_records(session, context, workflow.id)
        run = new_run(context, workflow, idempotency_key=idempotency_key, retry_of=retry_of, schedule_id=schedule_id)
        if not self._insert_run(session, run):
            return None
        if workflow.overlap_policy == OverlapPolicy.CANCEL_PREVIOUS:
            for previous_record in active:
                previous = cancel_state(_run(previous_record, workflow.name))
                self._save_run_state(previous_record, previous)
                announce(previous, context, effects, workflow_creator=workflow.created_by)
        execute_run(run, context, effects, workflow_creator=workflow.created_by)
        record = self._run_record(session, context, run.id)
        self._save_run_state(record, run)
        session.flush()
        return run

    def start(
        self,
        context: TenantContext,
        workflow_id: str,
        idempotency_key: str | None = None,
        retry_of: str | None = None,
    ) -> WorkflowRun:
        with self._work(context) as (session, effects):
            # The workflow lock serialises starts, so the overlap check sees every active run.
            workflow_record = self._workflow_record(session, context, workflow_id, lock=True)
            if idempotency_key:
                existing = self._run_by_key(session, context, idempotency_key)
                if existing is not None:
                    return _run(existing, workflow_record.name)
            workflow = _workflow(workflow_record)
            overlap_check(workflow, [_run(r) for r in self._active_records(session, context, workflow_id)])
            run = self._launch(session, effects, context, workflow, idempotency_key=idempotency_key, retry_of=retry_of)
            if run is None:
                # A concurrent request with the same key won the insert; return its run.
                winner = self._run_by_key(session, context, idempotency_key or "")
                if winner is None:  # pragma: no cover - the conflicting row must be visible
                    raise AutomationConflict("Run with this idempotency key could not be read")
                return _run(winner, workflow.name)
            return run

    def _named_runs(self, statement: Select[Any], session: Session) -> list[WorkflowRun]:
        rows = session.execute(statement).all()
        return [_run(row[0], row[1]) for row in rows]

    def _runs_statement(self, context: TenantContext) -> Select[Any]:
        joined = select(R, W.name).outerjoin(
            W, and_(W.tenant_id == R.tenant_id, W.workspace_id == R.workspace_id, W.id == R.workflow_id)
        )
        return self._scoped(joined, R, context)

    def list_runs(self, context: TenantContext) -> list[WorkflowRun]:
        return self.page_runs(context, RunFilter(limit=10_000))[0]

    def page_runs(self, context: TenantContext, page: RunFilter) -> tuple[list[WorkflowRun], int]:
        with self._unit(context) as session:
            statement = self._runs_statement(context)
            counter = self._scoped(select(func.count()).select_from(R), R, context)
            for column, value in ((R.workflow_id, page.workflow_id), (R.schedule_id, page.schedule_id)):
                if value is not None:
                    statement, counter = statement.where(column == value), counter.where(column == value)
            if page.status is not None:
                statement, counter = statement.where(R.status == page.status.value), counter.where(R.status == page.status.value)
            total = session.scalar(counter) or 0
            runs = self._named_runs(
                statement.order_by(R.created_at.desc(), R.id.desc()).offset(page.offset).limit(page.limit), session
            )
            return runs, int(total)

    def get_run(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._unit(context) as session:
            runs = self._named_runs(self._runs_statement(context).where(R.id == run_id), session)
            if not runs:
                raise AutomationNotFound("run", run_id)
            return runs[0]

    def cancel(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._work(context) as (session, effects):
            record = self._run_record(session, context, run_id, lock=True)
            workflow = self._workflow_record(session, context, record.workflow_id)
            run = cancel_state(_run(record, workflow.name))
            self._save_run_state(record, run)
            announce(run, context, effects, workflow_creator=workflow.created_by)
            return run

    def resume(self, context: TenantContext, run_id: str) -> WorkflowRun:
        with self._work(context) as (session, effects):
            record = self._run_record(session, context, run_id, lock=True)
            workflow = self._workflow_record(session, context, record.workflow_id)
            run = resume_state(_run(record, workflow.name), context, effects, workflow_creator=workflow.created_by)
            self._save_run_state(record, run)
            return run

    # -- scheduler --------------------------------------------------------------------

    def due_schedule_scopes(self, *, limit: int = 100) -> list[tuple[str, str, str]]:
        rows = discover(self.maintenance_session_factory, _DUE_SCHEDULES, {"limit": limit})
        return [(row.id, row.tenant_id, row.workspace_id) for row in rows]

    def fire_schedule(self, schedule_id: str, tenant_id: str, workspace_id: str) -> WorkflowRun | None:
        """Claim one due schedule in its tenant context; None if another replica has it."""
        claim_context = scheduler_context(tenant_id, workspace_id, None)
        with self._work(claim_context) as (session, _claim_effects):
            claimed = session.execute(
                _CLAIM_SCHEDULE,
                {"id": schedule_id, "tenant_id": tenant_id, "workspace_id": workspace_id},
            ).mappings().one_or_none()
            if claimed is None:
                return None
            fire_at: datetime = claimed["next_run_at"]
            db_now: datetime = claimed["db_now"]
            context = scheduler_context(tenant_id, workspace_id, claimed["created_by"])
            effects = session_effects(session, context)
            run: WorkflowRun | None = None
            outcome = ScheduleOutcome.FIRED
            key = schedule_fire_key(schedule_id, fire_at)
            workflow_record = session.scalar(
                select(W)
                .where(W.tenant_id == tenant_id, W.workspace_id == workspace_id, W.id == claimed["workflow_id"])
                .with_for_update()
            )
            # An archived workspace is read-only: its schedules advance without running
            # (docs/identity.md#workspace-lifecycle).
            archived = session.execute(
                text("select archived_at from workspaces where tenant_id = :tenant_id and id = :workspace_id"),
                {"tenant_id": tenant_id, "workspace_id": workspace_id},
            ).scalar()
            if archived is not None:
                outcome = ScheduleOutcome.WORKSPACE_ARCHIVED
            elif workflow_record is None or workflow_record.status != WorkflowStatus.ACTIVE.value:
                outcome = ScheduleOutcome.WORKFLOW_PAUSED
            else:
                workflow = _workflow(workflow_record)
                if not effects.actor_may_run(context, context.user_id):
                    outcome = ScheduleOutcome.OWNER_INACTIVE
                    candidate = new_run(context, workflow, idempotency_key=key, schedule_id=schedule_id)
                    if self._insert_run(session, candidate):
                        run = fail_before_start(
                            candidate, context, effects, owner_inactive_message(context.user_id), workflow_creator=workflow.created_by
                        )
                        self._save_run_state(self._run_record(session, context, run.id), run)
                elif workflow.overlap_policy == OverlapPolicy.SKIP and self._active_records(session, context, workflow.id):
                    outcome = ScheduleOutcome.SKIPPED_OVERLAP
                else:
                    # None: this fire time already produced its run.
                    run = self._launch(session, effects, context, workflow, idempotency_key=key, schedule_id=schedule_id)
            # A missed fire runs once; the schedule then continues from now, no catch-up.
            session.execute(
                _ADVANCE_SCHEDULE,
                {
                    "next_run_at": next_fire(claimed["cron"], claimed["timezone"], max(db_now, fire_at)),
                    "last_run_at": db_now,
                    "last_outcome": outcome.value,
                    "updated_at": db_now,
                    "id": schedule_id,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                },
            )
        effects.flush()
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


__all__ = ["PostgresAutomationEngine", "SUMMARY_FIELDS", "session_effects"]
