from .audit import AuditRecord
from .schemas import (
    AgentRun,
    Approval,
    ApprovalApproverRecord,
    DomainEvent,
    Task,
    Tenant,
    Workspace,
    WorkspaceInvitation,
    WorkspaceApprovalPolicy,
    WorkspaceMembership,
)


class InMemoryStore:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, AgentRun] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[DomainEvent] = []
        self.tenants: dict[str, Tenant] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.memberships: dict[tuple[str, str, str], WorkspaceMembership] = {}
        self.invitations: dict[str, WorkspaceInvitation] = {}
        self.audit_records: list[AuditRecord] = []
        self.approval_policies: dict[tuple[str, str], WorkspaceApprovalPolicy] = {}
        # approval id -> recorded approvers, oldest first (approval chains).
        self.approval_approvers: dict[str, list[ApprovalApproverRecord]] = {}


store = InMemoryStore()
