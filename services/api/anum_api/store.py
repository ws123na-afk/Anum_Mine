from .schemas import AgentRun, Approval, DomainEvent, Task


class InMemoryStore:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, AgentRun] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[DomainEvent] = []


store = InMemoryStore()
