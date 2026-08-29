from .schemas import AgentRun, Approval, DomainEvent, Task, Tenant, Workspace, WorkspaceMembership


class InMemoryStore:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, AgentRun] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[DomainEvent] = []
        self.tenants: dict[str, Tenant] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.memberships: dict[tuple[str, str, str], WorkspaceMembership] = {}


store = InMemoryStore()
