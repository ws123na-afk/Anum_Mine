from typing import Protocol

from .audit import AuditRecord, DuplicateAuditRecordError
from .schemas import (
    AgentRun,
    Approval,
    ApprovalApproverRecord,
    CallerMembership,
    DomainEvent,
    Task,
    TaskSummary,
    Tenant,
    TenantContext,
    Workspace,
    WorkspaceApprovalPolicy,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from .store import InMemoryStore
from .tasks import CREATORS_MAX, TaskFilters
from .tool_governance import ToolGovernance, tool_governance_from


class UnknownCursorError(LookupError):
    """A paging cursor that names no row in the caller's scope (the API answers 400)."""


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
    def list_tasks(
        self,
        context: TenantContext,
        *,
        limit: int | None = None,
        after: str | None = None,
        filters: TaskFilters | None = None,
    ) -> list[Task]: ...
    def summarize_tasks(self, context: TenantContext, filters: TaskFilters) -> TaskSummary: ...
    def list_runs_for_task(
        self, task_id: str, context: TenantContext, *, limit: int | None = None, after: str | None = None
    ) -> list[AgentRun]: ...
    def list_task_events(
        self, task_id: str, context: TenantContext, *, limit: int | None = None, after: str | None = None
    ) -> list[DomainEvent]: ...
    def list_task_approvals(self, task_id: str, context: TenantContext) -> list[Approval]: ...
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
    def get_approval_policy(self, context: TenantContext) -> WorkspaceApprovalPolicy: ...
    def save_approval_policy(
        self, policy: WorkspaceApprovalPolicy, context: TenantContext
    ) -> WorkspaceApprovalPolicy: ...
    def get_tool_governance(self, context: TenantContext) -> ToolGovernance: ...
    def add_approval_approver(
        self, approver: ApprovalApproverRecord, context: TenantContext
    ) -> ApprovalApproverRecord: ...
    def list_approval_approvers(
        self, approval_ids: list[str], context: TenantContext
    ) -> dict[str, list[ApprovalApproverRecord]]: ...


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

    def _scoped_tasks(self, context: TenantContext) -> list[Task]:
        return [
            task
            for task in self.store.tasks.values()
            if task.tenant_id == context.tenant_id and task.workspace_id == context.workspace_id
        ]

    def list_tasks(
        self,
        context: TenantContext,
        *,
        limit: int | None = None,
        after: str | None = None,
        filters: TaskFilters | None = None,
    ) -> list[Task]:
        """Newest first; ``after`` is the id of the last task already shown (keyset paging).

        Without ``filters`` every task is listed (archived ones too), as before filters
        existed; the route always passes filters.
        """
        tasks = self._scoped_tasks(context)
        if after is not None:
            anchor = next((task for task in tasks if task.id == after), None)
            if anchor is None:
                raise UnknownCursorError(after)
            key = (anchor.created_at, anchor.id)
            tasks = [task for task in tasks if (task.created_at, task.id) < key]
        if filters is not None:
            tasks = [task for task in tasks if filters.matches(task)]
        tasks.sort(key=lambda task: (task.created_at, task.id), reverse=True)
        return tasks if limit is None else tasks[:limit]

    def summarize_tasks(self, context: TenantContext, filters: TaskFilters) -> TaskSummary:
        tasks = self._scoped_tasks(context)
        unfiltered = filters.without_status()
        by_status: dict[str, int] = {}
        for task in tasks:
            if unfiltered.matches(task):
                by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
        archived_filter = unfiltered.with_archived(True)
        creators = sorted({task.created_by for task in tasks if task.created_by})[:CREATORS_MAX]
        return TaskSummary(
            total=sum(by_status.values()),
            by_status=by_status,
            archived=sum(1 for task in tasks if archived_filter.matches(task)),
            creators=creators,
        )

    def list_runs_for_task(
        self, task_id: str, context: TenantContext, *, limit: int | None = None, after: str | None = None
    ) -> list[AgentRun]:
        """The task's runs newest first, keyset-paged on ``(created_at, id)``."""
        if not self.get_task(task_id, context):
            return []
        runs = [run for run in self.store.runs.values() if run.task_id == task_id]
        if after is not None:
            anchor = next((run for run in runs if run.id == after), None)
            if anchor is None:
                raise UnknownCursorError(after)
            key = (anchor.created_at, anchor.id)
            runs = [run for run in runs if (run.created_at, run.id) < key]
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        return runs if limit is None else runs[:limit]

    def list_task_events(
        self, task_id: str, context: TenantContext, *, limit: int | None = None, after: str | None = None
    ) -> list[DomainEvent]:
        """The task's events (correlation id = task id) newest first, keyset-paged."""
        if not self.get_task(task_id, context):
            return []
        events = [
            event
            for event in self.list_events(context)
            if event.correlation_id == task_id and event.workspace_id == context.workspace_id
        ]
        if after is not None:
            anchor = next((event for event in events if event.id == after), None)
            if anchor is None:
                raise UnknownCursorError(after)
            key = (anchor.created_at, anchor.id)
            events = [event for event in events if (event.created_at, event.id) < key]
        events.sort(key=lambda event: (event.created_at, event.id), reverse=True)
        return events if limit is None else events[:limit]

    def list_task_approvals(self, task_id: str, context: TenantContext) -> list[Approval]:
        if not self.get_task(task_id, context):
            return []
        return sorted(
            (approval for approval in self.store.approvals.values() if approval.task_id == task_id),
            key=lambda approval: (approval.created_at, approval.id),
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
        """The task's newest run (a retried task has several)."""
        runs = self.list_runs_for_task(task_id, context, limit=1)
        return runs[0] if runs else None

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

    def get_approval_policy(self, context: TenantContext) -> WorkspaceApprovalPolicy:
        stored = self.store.approval_policies.get((context.tenant_id, context.workspace_id))
        return stored.model_copy() if stored is not None else WorkspaceApprovalPolicy()

    def save_approval_policy(
        self, policy: WorkspaceApprovalPolicy, context: TenantContext
    ) -> WorkspaceApprovalPolicy:
        self.store.approval_policies[(context.tenant_id, context.workspace_id)] = policy.model_copy()
        return policy

    def get_tool_governance(self, context: TenantContext) -> ToolGovernance:
        """The tenant's enabled approval rules and active policy pack rules.

        The in-memory backend keeps governance in ``governance.governance_store`` (the
        store behind the governance routes), filtered to the caller's tenant.
        """
        from .governance import governance_store

        return tool_governance_from(
            governance_store.list_approval_rules(context), governance_store.list_policy_packs(context)
        )

    def add_approval_approver(
        self, approver: ApprovalApproverRecord, context: TenantContext
    ) -> ApprovalApproverRecord:
        """Record one approve decision; a user can approve an approval only once."""
        if (approver.tenant_id, approver.workspace_id) != (context.tenant_id, context.workspace_id):
            raise ValueError("Approver must belong to the caller's tenant and workspace")
        if self.get_approval(approver.approval_id, context) is None:
            raise ValueError(f"Approval not found: {approver.approval_id!r}")
        recorded = self.store.approval_approvers.setdefault(approver.approval_id, [])
        if any(existing.user_id == approver.user_id for existing in recorded):
            raise ValueError(f"User already approved {approver.approval_id!r}")
        recorded.append(approver.model_copy())
        return approver

    def list_approval_approvers(
        self, approval_ids: list[str], context: TenantContext
    ) -> dict[str, list[ApprovalApproverRecord]]:
        result: dict[str, list[ApprovalApproverRecord]] = {}
        for approval_id in approval_ids:
            rows = [
                row
                for row in self.store.approval_approvers.get(approval_id, [])
                if row.tenant_id == context.tenant_id and row.workspace_id == context.workspace_id
            ]
            if rows:
                result[approval_id] = sorted(rows, key=lambda row: (row.approved_at, row.user_id))
        return result

    def list_caller_memberships(self, context: TenantContext) -> list[CallerMembership]:
        """The caller's active memberships in every workspace of their tenant."""
        found = []
        for (tenant_id, workspace_id, user_id), membership in self.store.memberships.items():
            if tenant_id != context.tenant_id or user_id != context.user_id or not membership.active:
                continue
            workspace = self.store.workspaces.get(workspace_id)
            name = workspace.name if workspace is not None and workspace.tenant_id == tenant_id else None
            found.append(
                CallerMembership(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    workspace_name=name,
                    role=membership.role,
                )
            )
        return sorted(found, key=lambda item: ((item.workspace_name or "").casefold(), item.workspace_id))
