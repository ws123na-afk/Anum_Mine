from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from anum_api.repository import AnumRepository
from anum_api.schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    Task,
    TaskStatus,
    Tenant,
    TenantContext,
    Workspace,
    WorkspaceMembership,
)

from .models import (
    AgentRunRecord,
    AgentRunStepRecord,
    ApprovalRecord,
    DomainEventRecord,
    TaskRecord,
    Tenant as TenantRecord,
    Workspace as WorkspaceRecord,
    WorkspaceMembershipRecord,
)


class SqlAlchemyRepository(AnumRepository):
    """Synchronous SQLAlchemy implementation of the ANUM repository contract."""

    def __init__(self, session: Session, created_by_user_id: str | None = None) -> None:
        self.session = session
        self.created_by_user_id = (
            created_by_user_id or session.info.get("user_id") or "system"
        )

    def create_tenant(self, tenant: Tenant) -> Tenant:
        if self.session.get(TenantRecord, tenant.id) is not None:
            raise ValueError(f"Tenant already exists: {tenant.id}")
        record = TenantRecord(
            id=tenant.id,
            name=tenant.name,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
        self.session.add(record)
        self.session.flush()
        return self._tenant_from_record(record)

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        record = self.session.get(TenantRecord, tenant_id)
        return self._tenant_from_record(record) if record else None

    def create_workspace(self, workspace: Workspace) -> Workspace:
        if self.session.get(WorkspaceRecord, workspace.id) is not None:
            raise ValueError(f"Workspace already exists: {workspace.id}")
        record = WorkspaceRecord(
            id=workspace.id,
            tenant_id=workspace.tenant_id,
            name=workspace.name,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        )
        self.session.add(record)
        self.session.flush()
        return self._workspace_from_record(record)

    def get_workspace(self, workspace_id: str, context: TenantContext) -> Workspace | None:
        record = self.session.scalar(
            select(WorkspaceRecord).where(
                WorkspaceRecord.id == workspace_id,
                WorkspaceRecord.tenant_id == context.tenant_id,
            )
        )
        return self._workspace_from_record(record) if record else None

    def save_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        key = (membership.user_id, membership.tenant_id, membership.workspace_id)
        record = self.session.get(WorkspaceMembershipRecord, key)
        if record is None:
            record = WorkspaceMembershipRecord(
                user_id=membership.user_id,
                tenant_id=membership.tenant_id,
                workspace_id=membership.workspace_id,
            )
            self.session.add(record)
        record.role = membership.role
        record.active = membership.active
        record.created_at = membership.created_at
        record.updated_at = membership.updated_at
        self.session.flush()
        return self._membership_from_record(record)

    def get_membership(self, context: TenantContext) -> WorkspaceMembership | None:
        record = self.session.get(
            WorkspaceMembershipRecord,
            (context.user_id, context.tenant_id, context.workspace_id),
        )
        return self._membership_from_record(record) if record else None

    def create_task(self, task: Task) -> Task:
        record = self.session.get(TaskRecord, task.id)
        if record is None:
            record = TaskRecord(
                id=task.id,
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                created_by_user_id=self.created_by_user_id,
            )
            self.session.add(record)
        elif record.tenant_id != task.tenant_id or record.workspace_id != task.workspace_id:
            raise ValueError(f"Task {task.id!r} cannot be moved between tenant scopes")

        record.title = task.title
        record.prompt = task.prompt
        record.status = task.status.value
        record.created_at = task.created_at
        record.updated_at = task.updated_at
        self.session.flush()
        return self._task_from_record(record)

    def list_tasks(self, context: TenantContext) -> list[Task]:
        records = self.session.scalars(
            select(TaskRecord)
            .where(
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
            .order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())
        ).all()
        return [self._task_from_record(record) for record in records]

    def save_task(self, task: Task) -> Task:
        return self.create_task(task)

    def get_task(self, task_id: str, context: TenantContext) -> Task | None:
        record = self.session.scalar(
            select(TaskRecord).where(
                TaskRecord.id == task_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
        )
        return self._task_from_record(record) if record is not None else None

    def get_task_for_update(self, task_id: str, context: TenantContext) -> Task | None:
        record = self.session.scalar(
            select(TaskRecord)
            .where(
                TaskRecord.id == task_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
            .with_for_update()
        )
        return self._task_from_record(record) if record is not None else None

    def save_run(self, run: AgentRun) -> AgentRun:
        task = self.session.get(TaskRecord, run.task_id)
        if task is None:
            raise ValueError(f"Cannot save run for missing task {run.task_id!r}")

        record = self.session.get(AgentRunRecord, run.id)
        if record is None:
            record = AgentRunRecord(
                id=run.id,
                task_id=run.task_id,
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
            )
            self.session.add(record)
        elif (
            record.task_id != run.task_id
            or record.tenant_id != task.tenant_id
            or record.workspace_id != task.workspace_id
        ):
            raise ValueError(f"Run {run.id!r} cannot be moved between task scopes")

        record.status = run.status.value
        record.result = run.result
        record.checkpoint = run.checkpoint.model_dump(mode="json")
        record.created_at = run.created_at
        record.updated_at = run.updated_at
        self._sync_steps(record, run.steps, task.tenant_id, task.workspace_id)
        self.session.flush()
        return self._run_from_record(record)

    def get_run(self, run_id: str, context: TenantContext) -> AgentRun | None:
        record = self.session.scalar(
            select(AgentRunRecord)
            .join(TaskRecord, AgentRunRecord.task_id == TaskRecord.id)
            .where(
                AgentRunRecord.id == run_id,
                AgentRunRecord.tenant_id == context.tenant_id,
                AgentRunRecord.workspace_id == context.workspace_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
        )
        return self._run_from_record(record) if record is not None else None

    def find_run_for_task(self, task_id: str, context: TenantContext) -> AgentRun | None:
        record = self.session.scalar(
            select(AgentRunRecord)
            .join(TaskRecord, AgentRunRecord.task_id == TaskRecord.id)
            .where(
                AgentRunRecord.task_id == task_id,
                AgentRunRecord.tenant_id == context.tenant_id,
                AgentRunRecord.workspace_id == context.workspace_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
            .order_by(AgentRunRecord.created_at.desc(), AgentRunRecord.id.desc())
            .limit(1)
        )
        return self._run_from_record(record) if record is not None else None

    def save_approval(self, approval: Approval) -> Approval:
        task = self.session.get(TaskRecord, approval.task_id)
        if task is None:
            raise ValueError(f"Cannot save approval for missing task {approval.task_id!r}")

        record = self.session.get(ApprovalRecord, approval.id)
        if record is None:
            record = ApprovalRecord(
                id=approval.id,
                task_id=approval.task_id,
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
            )
            self.session.add(record)
        elif (
            record.task_id != approval.task_id
            or record.tenant_id != task.tenant_id
            or record.workspace_id != task.workspace_id
        ):
            raise ValueError(f"Approval {approval.id!r} cannot be moved between task scopes")

        record.action = approval.action
        record.risk_level = approval.risk_level.value
        record.status = approval.status.value
        record.reason = approval.reason
        record.created_at = approval.created_at
        record.decided_at = approval.decided_at
        self.session.flush()
        return self._approval_from_record(record)

    def get_approval(self, approval_id: str, context: TenantContext) -> Approval | None:
        record = self.session.scalar(
            select(ApprovalRecord)
            .join(TaskRecord, ApprovalRecord.task_id == TaskRecord.id)
            .where(
                ApprovalRecord.id == approval_id,
                ApprovalRecord.tenant_id == context.tenant_id,
                ApprovalRecord.workspace_id == context.workspace_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
        )
        return self._approval_from_record(record) if record is not None else None

    def get_approval_for_update(
        self, approval_id: str, context: TenantContext
    ) -> Approval | None:
        record = self.session.scalar(
            select(ApprovalRecord)
            .join(TaskRecord, ApprovalRecord.task_id == TaskRecord.id)
            .where(
                ApprovalRecord.id == approval_id,
                ApprovalRecord.tenant_id == context.tenant_id,
                ApprovalRecord.workspace_id == context.workspace_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
            .with_for_update(of=ApprovalRecord)
        )
        return self._approval_from_record(record) if record is not None else None

    def list_approvals(self, context: TenantContext) -> list[Approval]:
        return self._list_approvals(context, for_update=False)

    def list_approvals_for_update(self, context: TenantContext) -> list[Approval]:
        return self._list_approvals(context, for_update=True)

    def _list_approvals(
        self, context: TenantContext, *, for_update: bool
    ) -> list[Approval]:
        statement = (
            select(ApprovalRecord)
            .join(TaskRecord, ApprovalRecord.task_id == TaskRecord.id)
            .where(
                ApprovalRecord.tenant_id == context.tenant_id,
                ApprovalRecord.workspace_id == context.workspace_id,
                TaskRecord.tenant_id == context.tenant_id,
                TaskRecord.workspace_id == context.workspace_id,
            )
            .order_by(ApprovalRecord.created_at, ApprovalRecord.id)
        )
        if for_update:
            statement = statement.with_for_update(of=ApprovalRecord)
        records = self.session.scalars(
            statement
        ).all()
        return [self._approval_from_record(record) for record in records]

    def list_events(self, context: TenantContext) -> list[DomainEvent]:
        records = self.session.scalars(
            select(DomainEventRecord)
            .where(
                DomainEventRecord.tenant_id == context.tenant_id,
                or_(
                    DomainEventRecord.workspace_id.is_(None),
                    DomainEventRecord.workspace_id == context.workspace_id,
                ),
            )
            .order_by(DomainEventRecord.created_at, DomainEventRecord.id)
        ).all()
        return [self._event_from_record(record) for record in records]

    def record_event(self, event: DomainEvent) -> DomainEvent:
        record = self.session.get(DomainEventRecord, event.id)
        if record is None:
            record = DomainEventRecord(id=event.id)
            self.session.add(record)
        elif record.tenant_id != event.tenant_id or record.workspace_id != event.workspace_id:
            raise ValueError(f"Event {event.id!r} cannot be moved between tenant scopes")

        record.type = event.type
        record.version = event.version
        record.tenant_id = event.tenant_id
        record.workspace_id = event.workspace_id
        record.subject = event.subject
        record.correlation_id = event.correlation_id
        record.payload = dict(event.payload)
        record.created_at = event.created_at
        self.session.flush()
        return self._event_from_record(record)

    def _sync_steps(
        self,
        run_record: AgentRunRecord,
        steps: Sequence[AgentRunStep],
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        persisted = {
            record.id: record
            for record in self.session.scalars(
                select(AgentRunStepRecord).where(
                    AgentRunStepRecord.run_id == run_record.id,
                    AgentRunStepRecord.tenant_id == tenant_id,
                    AgentRunStepRecord.workspace_id == workspace_id,
                )
            )
        }
        requested_ids: set[str] = set()

        for step in steps:
            if step.id in requested_ids:
                raise ValueError(f"Run {run_record.id!r} contains duplicate step {step.id!r}")
            requested_ids.add(step.id)

            record = persisted.get(step.id)
            if record is None:
                record = self.session.get(AgentRunStepRecord, step.id)
                if record is not None and record.run_id != run_record.id:
                    raise ValueError(f"Step {step.id!r} already belongs to another run")
            if record is None:
                record = AgentRunStepRecord(
                    id=step.id,
                    run_id=run_record.id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                self.session.add(record)
            elif record.tenant_id != tenant_id or record.workspace_id != workspace_id:
                raise ValueError(f"Step {step.id!r} cannot be moved between tenant scopes")

            record.type = step.type
            record.summary = step.summary
            record.step_metadata = dict(step.metadata)
            record.created_at = step.created_at

        for step_id, record in persisted.items():
            if step_id not in requested_ids:
                self.session.delete(record)

    def _run_from_record(self, record: AgentRunRecord) -> AgentRun:
        steps = self.session.scalars(
            select(AgentRunStepRecord)
            .where(
                AgentRunStepRecord.run_id == record.id,
                AgentRunStepRecord.tenant_id == record.tenant_id,
                AgentRunStepRecord.workspace_id == record.workspace_id,
            )
            .order_by(AgentRunStepRecord.created_at, AgentRunStepRecord.id)
        ).all()
        return AgentRun(
            id=record.id,
            task_id=record.task_id,
            status=TaskStatus(record.status),
            steps=[self._step_from_record(step) for step in steps],
            result=record.result,
            checkpoint=record.checkpoint or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _task_from_record(record: TaskRecord) -> Task:
        return Task(
            id=record.id,
            title=record.title,
            prompt=record.prompt,
            status=TaskStatus(record.status),
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _tenant_from_record(record: TenantRecord) -> Tenant:
        return Tenant(
            id=record.id,
            name=record.name,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _workspace_from_record(record: WorkspaceRecord) -> Workspace:
        return Workspace(
            id=record.id,
            tenant_id=record.tenant_id,
            name=record.name,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _membership_from_record(record: WorkspaceMembershipRecord) -> WorkspaceMembership:
        return WorkspaceMembership(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            role=record.role,
            active=record.active,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _step_from_record(record: AgentRunStepRecord) -> AgentRunStep:
        return AgentRunStep(
            id=record.id,
            type=record.type,
            summary=record.summary,
            created_at=record.created_at,
            metadata=dict(record.step_metadata),
        )

    @staticmethod
    def _approval_from_record(record: ApprovalRecord) -> Approval:
        return Approval(
            id=record.id,
            task_id=record.task_id,
            action=record.action,
            risk_level=RiskLevel(record.risk_level),
            status=ApprovalStatus(record.status),
            reason=record.reason,
            created_at=record.created_at,
            decided_at=record.decided_at,
        )

    @staticmethod
    def _event_from_record(record: DomainEventRecord) -> DomainEvent:
        return DomainEvent(
            id=record.id,
            type=record.type,
            version=record.version,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            subject=record.subject,
            correlation_id=record.correlation_id,
            created_at=record.created_at,
            payload=dict(record.payload),
        )


PostgresRepository = SqlAlchemyRepository
SQLAlchemyRepository = SqlAlchemyRepository
