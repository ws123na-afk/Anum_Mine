from .schemas import AgentRun, Approval, DomainEvent, FileObject, Task


class InMemoryStore:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, AgentRun] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[DomainEvent] = []
        self.files: dict[str, FileObject] = {}


store = InMemoryStore()
