from typing import Protocol

from .audit import AuditRecord, DuplicateAuditRecordError
from .schemas import (
    AgentRun,
    Approval,
    DomainEvent,
    Task,
    Tenant,
    TenantContext,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from .store import InMemoryStore


class AnumRepository(Protocol):
    def create_tenant(self, tenant: Tenant) -> Tenant: ...
    def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    def create_workspace(self, workspace: Workspace) -> Workspace: ...
    def get_workspace(self, workspace_id: str, context: TenantContext) -> Workspace | None: ...
    def save_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership: ...
    def get_membership(self, context: TenantContext) -> WorkspaceMembership | None: ...
    def workspace_has_members(self, context: TenantContext) -> bool: ...
    def list_memberships(self, context: TenantContext) -> list[WorkspaceMembership]: ...
    def get_member_for_update(
        self, user_id: str, context: TenantContext
    ) -> WorkspaceMembership | None: ...
    def list_active_owners_for_update(
        self, context: TenantContext
    ) -> list[WorkspaceMembership]: ...
    def save_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation: ...
    def get_invitation_for_update(
        self, invitation_id: str, context: TenantContext
    ) -> WorkspaceInvitation | None: ...
    def find_invitation_by_token_hash_for_update(
        self, token_hash: str, context: TenantContext
    ) -> WorkspaceInvitation | None: ...
    def list_invitations(self, context: TenantContext) -> list[WorkspaceInvitation]: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...
    def list_audit_records(self, context: TenantContext) -> list[AuditRecord]: ...
    def create_task(self, task: Task) -> Task: ...
    def list_tasks(self, context: TenantContext) -> list[Task]: ...
    def save_task(self, task: Task) -> Task: ...
    def get_task(self, task_id: str, context: TenantContext) -> Task | None: ...
    def get_task_for_update(self, task_id: str, context: TenantContext) -> Task | None: ...
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

    def create_tenant(self, tenant: Tenant) -> Tenant:
        if tenant.id in self.store.tenants:
            raise ValueError(f"Tenant already exists: {tenant.id}")
        self.store.tenants[tenant.id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.store.tenants.get(tenant_id)

    def create_workspace(self, workspace: Workspace) -> Workspace:
        if workspace.id in self.store.workspaces:
            raise ValueError(f"Workspace already exists: {workspace.id}")
        if workspace.tenant_id not in self.store.tenants:
            raise ValueError(f"Tenant does not exist: {workspace.tenant_id}")
        self.store.workspaces[workspace.id] = workspace
        return workspace

    def get_workspace(self, workspace_id: str, context: TenantContext) -> Workspace | None:
        workspace = self.store.workspaces.get(workspace_id)
        return workspace if workspace and workspace.tenant_id == context.tenant_id else None

    def save_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        key = (membership.tenant_id, membership.workspace_id, membership.user_id)
        self.store.memberships[key] = membership
        return membership

    def get_membership(self, context: TenantContext) -> WorkspaceMembership | None:
        return self.store.memberships.get(
            (context.tenant_id, context.workspace_id, context.user_id)
        )

    def workspace_has_members(self, context: TenantContext) -> bool:
        return any(
            tenant_id == context.tenant_id and workspace_id == context.workspace_id
            for tenant_id, workspace_id, _ in self.store.memberships
        )

    def list_memberships(self, context: TenantContext) -> list[WorkspaceMembership]:
        return sorted(
            (
                membership
                for (tenant_id, workspace_id, _), membership in self.store.memberships.items()
                if tenant_id == context.tenant_id and workspace_id == context.workspace_id
            ),
            key=lambda membership: (membership.created_at, membership.user_id),
        )

    def get_member_for_update(
        self, user_id: str, context: TenantContext
    ) -> WorkspaceMembership | None:
        return self.store.memberships.get((context.tenant_id, context.workspace_id, user_id))

    def list_active_owners_for_update(
        self, context: TenantContext
    ) -> list[WorkspaceMembership]:
        return [
            membership
            for membership in self.list_memberships(context)
            if membership.active and membership.role == "owner"
        ]

    def save_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        existing = self.store.invitations.get(invitation.id)
        if existing is not None and (
            existing.tenant_id != invitation.tenant_id
            or existing.workspace_id != invitation.workspace_id
        ):
            raise ValueError(f"Invitation {invitation.id!r} cannot be moved between scopes")
        if any(
            other.token_hash == invitation.token_hash and other.id != invitation.id
            for other in self.store.invitations.values()
        ):
            raise ValueError("Invitation token collision")
        self.store.invitations[invitation.id] = invitation.model_copy(deep=True)
        return invitation

    def _scoped_invitation(
        self, invitation: WorkspaceInvitation | None, context: TenantContext
    ) -> WorkspaceInvitation | None:
        if invitation is None:
            return None
        if (
            invitation.tenant_id != context.tenant_id
            or invitation.workspace_id != context.workspace_id
        ):
            return None
        return invitation.model_copy(deep=True)

    def get_invitation_for_update(
        self, invitation_id: str, context: TenantContext
    ) -> WorkspaceInvitation | None:
        return self._scoped_invitation(self.store.invitations.get(invitation_id), context)

    def find_invitation_by_token_hash_for_update(
        self, token_hash: str, context: TenantContext
    ) -> WorkspaceInvitation | None:
        match = next(
            (
                invitation
                for invitation in self.store.invitations.values()
                if invitation.token_hash == token_hash
            ),
            None,
        )
        return self._scoped_invitation(match, context)

    def list_invitations(self, context: TenantContext) -> list[WorkspaceInvitation]:
        scoped = (
            self._scoped_invitation(invitation, context)
            for invitation in self.store.invitations.values()
        )
        return sorted(
            (invitation for invitation in scoped if invitation is not None),
            key=lambda invitation: (invitation.created_at, invitation.id),
            reverse=True,
        )

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        if any(existing.id == record.id for existing in self.store.audit_records):
            raise DuplicateAuditRecordError(f"audit record already exists: {record.id}")
        self.store.audit_records.append(record)
        return record

    def list_audit_records(self, context: TenantContext) -> list[AuditRecord]:
        return sorted(
            (
                record
                for record in self.store.audit_records
                if record.tenant_id == context.tenant_id
                and record.workspace_id == context.workspace_id
            ),
            key=lambda record: (record.created_at, record.id),
        )

    def create_task(self, task: Task) -> Task:
        return self.save_task(task)

    def list_tasks(self, context: TenantContext) -> list[Task]:
        return sorted(
            (
                task
                for task in self.store.tasks.values()
                if task.tenant_id == context.tenant_id
                and task.workspace_id == context.workspace_id
            ),
            key=lambda task: (task.created_at, task.id),
            reverse=True,
        )

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
