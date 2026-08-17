from typing import Protocol

from .schemas import AgentRun, Approval, DomainEvent, Task, TenantContext
from .store import InMemoryStore


class AnumRepository(Protocol):
    def create_task(self, task: Task) -> Task: ...
    def save_task(self, task: Task) -> Task: ...
    def get_task(self, task_id: str, context: TenantContext) -> Task | None: ...
    def get_task_for_update(self, task_id: str, context: TenantContext) -> Task | None: ...
    def list_tasks(self, context: TenantContext) -> list[Task]: ...
    def save_run(self, run: AgentRun) -> AgentRun: ...
    def get_run(self, run_id: str, context: TenantContext) -> AgentRun | None: ...
    def find_run_for_task(self, task_id: str, context: TenantContext) -> AgentRun | None: ...
    def save_approval(self, approval: Approval) -> Approval: ...
    def get_approval(self, approval_id: str, context: TenantContext) -> Approval | None: ...
    def get_approval_for_update(
        self, approval_id: str, context: TenantContext
    ) -> Approval | None: ...
    def list_approvals(self, context: TenantContext) -> list[Approval]: ...
    def list_approvals_for_update(self, context: TenantContext) -> list[Approval]: ...
    def list_events(self, context: TenantContext) -> list[DomainEvent]: ...
    def record_event(self, event: DomainEvent) -> DomainEvent: ...


class InMemoryRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def create_task(self, task: Task) -> Task:
        return self.save_task(task)

    def save_task(self, task: Task) -> Task:
        self.store.tasks[task.id] = task
        return task

    def get_task(self, task_id: str, context: TenantContext) -> Task | None:
        task = self.store.tasks.get(task_id)
        if not task:
            return None
        if task.tenant_id != context.tenant_id or task.workspace_id != context.workspace_id:
            return None
        return task

    def get_task_for_update(self, task_id: str, context: TenantContext) -> Task | None:
        return self.get_task(task_id, context)

    def list_tasks(self, context: TenantContext) -> list[Task]:
        tasks = [
            task
            for task in self.store.tasks.values()
            if task.tenant_id == context.tenant_id and task.workspace_id == context.workspace_id
        ]
        return sorted(tasks, key=lambda task: (task.created_at, task.id))

    def save_run(self, run: AgentRun) -> AgentRun:
        self.store.runs[run.id] = run
        return run

    def get_run(self, run_id: str, context: TenantContext) -> AgentRun | None:
        run = self.store.runs.get(run_id)
        if not run:
            return None
        task = self.get_task(run.task_id, context)
        return run if task else None

    def find_run_for_task(self, task_id: str, context: TenantContext) -> AgentRun | None:
        if not self.get_task(task_id, context):
            return None
        return next((run for run in self.store.runs.values() if run.task_id == task_id), None)

    def save_approval(self, approval: Approval) -> Approval:
        self.store.approvals[approval.id] = approval
        return approval

    def get_approval(self, approval_id: str, context: TenantContext) -> Approval | None:
        approval = self.store.approvals.get(approval_id)
        if not approval:
            return None
        return approval if self.get_task(approval.task_id, context) else None

    def get_approval_for_update(
        self, approval_id: str, context: TenantContext
    ) -> Approval | None:
        return self.get_approval(approval_id, context)

    def list_approvals(self, context: TenantContext) -> list[Approval]:
        return [
            approval
            for approval in self.store.approvals.values()
            if self.get_task(approval.task_id, context)
        ]

    def list_events(self, context: TenantContext) -> list[DomainEvent]:
        return [
            event
            for event in self.store.events
            if event.tenant_id == context.tenant_id
            and (event.workspace_id is None or event.workspace_id == context.workspace_id)
        ]

    def list_approvals_for_update(self, context: TenantContext) -> list[Approval]:
        return self.list_approvals(context)

    def record_event(self, event: DomainEvent) -> DomainEvent:
        self.store.events.append(event)
        return event
