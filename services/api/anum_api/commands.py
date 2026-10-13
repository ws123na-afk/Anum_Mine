"""ANUM Command: one spoken or typed request, planned, confirmed and run (docs/command.md).

The flow behind the Command screens (mobile and the web Command Center):

1. ``POST /api/v1/commands/preview`` reads the request and returns the plan it would
   run: ordered steps, the agent, skills and tool each step uses, the step's risk and
   whether a run will pause for approval (or be blocked). Nothing is created or run and
   no model is called: the steps come from splitting the request into clauses and the
   tools from the runtime's own skill selection and tool choice
   (``agent_skills.SkillRegistry.select``, ``agent_planning.choose_tool_call``); the
   approval answer is the runtime's own policy decision (``ToolPolicy`` plus the
   workspace's autonomy policy).
2. ``POST /api/v1/commands`` creates the command record and one ordinary task per
   confirmed step (in the confirmed order). Each step is planned again on the server, so
   what the client sends cannot change an agent, risk or approval. Clients then start
   every task with ``POST /api/v1/tasks/{id}/run`` (in parallel) and stop them with
   ``POST /api/v1/tasks/bulk {"action": "cancel"}``.
3. ``GET /api/v1/commands/{id}`` is the live view: each step's task, latest run and
   approvals (with payload hashes), the command's derived status and progress, a
   timeline built from the tasks' events and the files (attachments and run logs).
   Clients refresh it when ``GET /api/v1/events/stream`` delivers an event of one of the
   command's tasks.
4. ``GET /api/v1/commands/{id}/report`` is the full report (JSON or Markdown) and
   ``GET /api/v1/commands/{id}/next-steps`` the suggested next actions derived from the
   results; each starts a new command.

``GET /api/v1/commands`` lists the caller's own commands and ``GET
/api/v1/commands/suggestions`` the chips for the Command screen: the caller's quick
commands and most used commands, and starters for what this deployment can do.
``/api/v1/quick-commands`` is the caller's saved commands and templates (Work, Personal,
Custom). Commands and quick commands are private to their user inside one tenant and
workspace (forced RLS on ``anum.user_id``, migration ``0044_commands``); the tasks they
create are ordinary workspace tasks. Creating, deleting and exporting are audited.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from .agent_planning import choose_tool_call
from .agent_skills import SkillManifest, SkillRegistry, default_skill_registry, prompt_words
from .agent_tools import ToolCall, ToolPolicy, ToolPolicyOutcome, ToolRegistry
from .approval_integrity import as_viewed
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
from .runtime import with_workspace_autonomy
from .schemas import (
    AgentRun,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from .settings import settings

router = APIRouter(prefix="/api/v1", tags=["commands"])

MAX_REQUEST_CHARS = 4000
MAX_INSTRUCTION_CHARS = 1000
MAX_STEPS = 8
MAX_ATTACHMENTS = 5
MAX_QUICK_COMMANDS = 100
MAX_TITLE_CHARS = 80
COMMAND_PAGE_DEFAULT = 20
COMMAND_PAGE_MAX = 50
TIMELINE_EVENTS_PER_TASK = 50
SUGGESTION_LIMIT = 8
NEXT_STEP_LIMIT = 6

Locale = Literal["en", "ar"]


# -- agents ------------------------------------------------------------------------------


class AgentId(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    COMMERCE = "commerce"
    RESEARCH = "research"
    ACTION = "action"
    CALENDAR = "calendar"
    TYRE = "tyre"
    WRITER = "writer"
    GENERAL = "general"


_AGENT_NAMES: dict[AgentId, tuple[str, str]] = {
    AgentId.EMAIL: ("Email agent", "وكيل البريد"),
    AgentId.WHATSAPP: ("WhatsApp agent", "وكيل واتساب"),
    AgentId.COMMERCE: ("Commerce agent", "وكيل المتجر"),
    AgentId.RESEARCH: ("Research agent", "وكيل البحث"),
    AgentId.ACTION: ("Action agent", "وكيل التنفيذ"),
    AgentId.CALENDAR: ("Calendar agent", "وكيل التقويم"),
    AgentId.TYRE: ("Tyre data agent", "وكيل بيانات الإطارات"),
    AgentId.WRITER: ("Report agent", "وكيل التقارير"),
    AgentId.GENERAL: ("Assistant", "المساعد"),
}

# What a step routed to an agent without a live integration actually does (shown in the
# plan, so nobody expects a calendar invite or a TyrePulse query that will not happen).
_AGENT_NOTES: dict[AgentId, tuple[str, str]] = {
    AgentId.CALENDAR: (
        "No calendar is connected to ANUM yet: this step drafts the meeting (time, attendees, agenda) for you to send.",
        "لا يوجد تقويم متصل بـ ANUM بعد: هذه الخطوة تجهّز مسودة الاجتماع (الوقت والحضور والجدول) لترسلها بنفسك.",
    ),
    AgentId.TYRE: (
        "Tyre planning is not available on this server: this step answers from the files and notes in this workspace.",
        "تخطيط الإطارات غير متاح على هذا الخادم: هذه الخطوة تجيب من الملفات والملاحظات الموجودة في مساحة العمل.",
    ),
}

# A tyre step whose tools exist but whose workspace has no tyre data connected yet: the
# run would fail with "Connect your tyre data first" (docs/tyre-planning.md).
_TYRE_CONNECT_NOTE = (
    "Connect TyrePulse under Tyre planning first: until then this step cannot read your fleet data.",
    "اربط TyrePulse من شاشة تخطيط الإطارات أولًا: حتى ذلك الحين لا تستطيع هذه الخطوة قراءة بيانات أسطولك.",
)

_TOOL_AGENTS: dict[str, AgentId] = {
    "search_email": AgentId.EMAIL,
    "list_whatsapp_conversations": AgentId.WHATSAPP,
    "web_search": AgentId.RESEARCH,
    "external.action": AgentId.ACTION,
}

_CALENDAR_WORDS = frozenset(
    {
        "meeting", "meetings", "schedule", "calendar", "appointment", "appointments", "invite", "reschedule",
        "اجتماع", "اجتماعا", "موعد", "مواعيد", "تقويم", "جدول",
    }
)
_TYRE_WORDS = frozenset(
    {
        "tyre", "tyres", "tire", "tires", "pmv", "tyrepulse", "fleet", "vehicle", "vehicles", "truck", "trucks",
        "إطار", "إطارات", "اطار", "اطارات", "أسطول", "اسطول", "مركبات", "شاحنات",
    }
)
_REPORT_WORDS = frozenset({"report", "reports", "summary", "summarise", "تقرير", "تقارير", "ملخص"})


class CommandAgent(BaseModel):
    id: AgentId
    name: str


def agent_view(agent: AgentId, locale: str) -> CommandAgent:
    english, arabic = _AGENT_NAMES[agent]
    return CommandAgent(id=agent, name=arabic if locale == "ar" else english)


def _tyre_tools() -> frozenset[str]:
    from .tyre.tools import TOOL_NAMES

    return frozenset(TOOL_NAMES)


def _agent_for(tool_name: str, skills: list[SkillManifest], words: set[str]) -> AgentId:
    agent = _TOOL_AGENTS.get(tool_name)
    if agent is not None:
        return agent
    if tool_name in _tyre_tools():
        return AgentId.TYRE
    if tool_name.startswith("commerce.") or any(skill.id == "anum.commerce" for skill in skills):
        return AgentId.COMMERCE
    if words & _CALENDAR_WORDS:
        return AgentId.CALENDAR
    if words & _TYRE_WORDS:
        return AgentId.TYRE
    if any(skill.id == "anum.document-drafting" for skill in skills) or words & _REPORT_WORDS:
        return AgentId.WRITER
    return AgentId.GENERAL


# -- splitting a request into steps ------------------------------------------------------

# Verbs that start a new step after "and" ("Check my emails and schedule a meeting").
_STEP_VERBS = (
    "check|find|prepare|schedule|send|create|identify|summarize|summarise|draft|write|search|look|call|"
    "compare|open|show|list|review|book|remind|set|update|analyze|analyse|generate|get|read|reply|make|plan|"
    "notify|share|email|publish|delete|pay"
)
_ARABIC_STEP_VERBS = "أرسل|ارسل|جهز|حضر|لخص|ابحث|تحقق|رتب|حدد|اكتب|أنشئ|انشئ|اعرض|راجع|احجز|ذكرني|قارن|شارك|احذف"
_SPLIT = re.compile(
    r"(?<!\b\w)[.!?؟;؛]+(?=\s|$)"  # sentence ends (not "a.m.")
    r"|\n+"
    r"|,(?!\d)|،"  # commas, but not "10,000"
    r"|\s+(?:and\s+)?then\s+|\s+after that\s+|\s+ثم\s+"
    rf"|\s+and\s+(?=(?:also\s+)?(?:{_STEP_VERBS})\b)"
    rf"|\s+و(?=(?:{_ARABIC_STEP_VERBS}))",
    re.IGNORECASE,
)
_LEADING = re.compile(r"^(?:and|also|then|please|و|ثم)\s+", re.IGNORECASE)


def split_request(text: str) -> list[str]:
    """The request's clauses, in order, without leading "and"/"then" and empty pieces."""
    clauses: list[str] = []
    for piece in _SPLIT.split(text):
        clause = " ".join(piece.split())
        while True:
            stripped = _LEADING.sub("", clause)
            if stripped == clause:
                break
            clause = stripped
        clause = clause.strip(" .,;:-–—")
        if len(clause) >= 2:
            clauses.append(clause)
    return clauses


def _title(text: str) -> str:
    value = text.strip()
    value = value[:1].upper() + value[1:]
    if len(value) > MAX_TITLE_CHARS:
        value = value[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return value


def language_of(text: str) -> Locale:
    arabic = len(re.findall(r"[؀-ۿ]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "ar" if arabic > latin else "en"


# -- planning ----------------------------------------------------------------------------


class ApprovalNeed(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    BLOCKED = "blocked"


class PlanSkill(BaseModel):
    id: str
    name: str


class PlanTool(BaseModel):
    name: str
    description: str
    category: str | None = None
    action_kind: str | None = None
    # The integration host the tool contacts (host only), or null for internal tools.
    target: str | None = None


class PlanStep(BaseModel):
    position: int
    title: str
    instruction: str
    agent: CommandAgent
    skills: list[PlanSkill]
    tool: PlanTool | None
    risk_level: RiskLevel
    approval: ApprovalNeed
    approval_reason: str
    # False when the step's agent has no live integration (calendar, TyrePulse): ``note``
    # then says what the step does instead.
    connected: bool = True
    note: str | None = None


class CommandPlan(BaseModel):
    text: str
    language: Locale
    steps: list[PlanStep]
    agents: list[CommandAgent]
    approvals_required: int
    blocked: int


class CommandPlanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    locale: Locale | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return " ".join(value.split())


class Planner:
    """Plans steps with the runtime's own skills, tool choice and policy (no model call)."""

    def __init__(
        self,
        tools: ToolRegistry,
        repository: AnumRepository,
        context: TenantContext,
        locale: str,
        skills: SkillRegistry | None = None,
    ) -> None:
        self.tools = tools
        self.skills = skills or default_skill_registry()
        self.policy = ToolPolicy(tools.names)
        self.repository = repository
        self.context = context
        self.locale = locale
        self._workspace_policy = repository.get_approval_policy(context)
        self._governance = repository.get_tool_governance(context)

    def _route(self, text: str) -> tuple[list[SkillManifest], ToolCall, AgentId]:
        skills = self.skills.select(text, self.tools.names)
        call = choose_tool_call(text, skills, "", self.tools.names)
        return skills, call, _agent_for(call.name, skills, prompt_words(text))

    def agent_of(self, text: str) -> AgentId:
        return self._route(text)[2]

    def step(self, position: int, instruction: str) -> PlanStep:
        skills, call, agent = self._route(instruction)
        definition = self.tools.definition(call.name)
        decision = self.policy.evaluate(call, definition, self.context, self._workspace_policy, self._governance)
        decision = with_workspace_autonomy(
            self.repository, call, definition, decision, self.context, tools=self.tools
        )
        approval = {
            ToolPolicyOutcome.ALLOW: ApprovalNeed.NONE,
            ToolPolicyOutcome.REQUIRE_APPROVAL: ApprovalNeed.REQUIRED,
            ToolPolicyOutcome.BLOCK: ApprovalNeed.BLOCKED,
        }[decision.outcome]
        note = self._note(agent, call.name)
        arabic = self.locale == "ar"
        return PlanStep(
            position=position,
            title=_title(instruction),
            instruction=instruction,
            agent=agent_view(agent, self.locale),
            skills=[PlanSkill(id=skill.id, name=skill.name) for skill in skills if skill.id != "anum.task-planning"],
            tool=None
            if definition is None
            else PlanTool(
                name=definition.name,
                description=definition.description,
                category=definition.category,
                action_kind=definition.action_kind,
                target=definition.target,
            ),
            risk_level=decision.risk_level,
            approval=approval,
            approval_reason=decision.reason,
            connected=note is None,
            note=None if note is None else (note[1] if arabic else note[0]),
        )

    _tyre_connection: bool | None = None

    def _tyre_connected(self) -> bool:
        """Whether the workspace saved a tyre data connection (one cached read per plan)."""
        if self._tyre_connection is None:
            from .tyre.store import open_tyre_store

            try:
                with open_tyre_store(self.context) as store:
                    self._tyre_connection = store.get_connection(self.context) is not None
            except Exception:  # unreadable: say "connect", the run reports the real error
                self._tyre_connection = False
        return self._tyre_connection

    def _note(self, agent: AgentId, tool_name: str) -> tuple[str, str] | None:
        """What a step does instead when its agent has no live integration, else None."""
        if agent == AgentId.TYRE and tool_name in _tyre_tools():
            return None if self._tyre_connected() else _TYRE_CONNECT_NOTE
        return _AGENT_NOTES.get(agent)

    def plan(self, text: str) -> CommandPlan:
        """Clauses become steps; a clause that names no agent of its own (or only asks for a
        summary) joins the step before it, as does one for the same agent."""
        groups: list[tuple[AgentId, list[str]]] = []
        for clause in split_request(text) or [text]:
            agent = self.agent_of(clause)
            if groups and (agent in {AgentId.GENERAL, AgentId.WRITER} or agent == groups[-1][0]):
                if groups[-1][0] in {AgentId.GENERAL, AgentId.WRITER} and agent not in {
                    AgentId.GENERAL,
                    AgentId.WRITER,
                }:
                    groups[-1] = (agent, groups[-1][1])
                groups[-1][1].append(clause)
                continue
            groups.append((agent, [clause]))
        # Past the step limit, the rest of the request stays together in the last step.
        if len(groups) > MAX_STEPS:
            tail = [clause for _, clauses in groups[MAX_STEPS - 1 :] for clause in clauses]
            groups = groups[: MAX_STEPS - 1] + [(groups[MAX_STEPS - 1][0], tail)]
        steps = [
            self.step(index, ", ".join(clauses)[:MAX_INSTRUCTION_CHARS]) for index, (_, clauses) in enumerate(groups, 1)
        ]
        return _plan_of(text, steps)


def _plan_of(text: str, steps: list[PlanStep]) -> CommandPlan:
    agents: dict[AgentId, CommandAgent] = {}
    for step in steps:
        agents.setdefault(step.agent.id, step.agent)
    return CommandPlan(
        text=text,
        language=language_of(text),
        steps=steps,
        agents=list(agents.values()),
        approvals_required=sum(1 for step in steps if step.approval == ApprovalNeed.REQUIRED),
        blocked=sum(1 for step in steps if step.approval == ApprovalNeed.BLOCKED),
    )


def _tools(request: Request) -> ToolRegistry:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:  # pragma: no cover - main.py always sets it
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The agent runtime is not configured")
    return registry  # type: ignore[no-any-return]


# -- stored records ----------------------------------------------------------------------


class CommandSource(StrEnum):
    TYPED = "typed"
    VOICE = "voice"
    QUICK_COMMAND = "quick_command"
    SUGGESTION = "suggestion"
    NEXT_STEP = "next_step"


class StoredStep(BaseModel):
    position: int
    task_id: str
    title: str
    instruction: str
    agent: AgentId
    tool: str | None = None
    risk_level: RiskLevel
    approval: ApprovalNeed


class CommandAttachment(BaseModel):
    file_id: str
    name: str
    content_type: str | None = None


class StoredCommand(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    text: str
    text_key: str
    source: CommandSource
    locale: Locale
    steps: list[StoredStep]
    attachments: list[CommandAttachment] = Field(default_factory=list)
    quick_command_id: str | None = None
    created_at: datetime
    updated_at: datetime


class QuickCommandCategory(StrEnum):
    WORK = "work"
    PERSONAL = "personal"
    CUSTOM = "custom"


class QuickCommand(BaseModel):
    id: str
    title: str
    text: str
    category: QuickCommandCategory
    position: int
    use_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def text_key(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!?؟")[:300]


class CommandStore(Protocol):
    def add_command(self, command: StoredCommand) -> StoredCommand: ...
    def get_command(self, command_id: str) -> StoredCommand | None: ...
    def list_commands(self, limit: int, after: str | None) -> list[StoredCommand]: ...
    def recent_texts(self, limit: int) -> list[tuple[str, str, datetime]]: ...
    def delete_command(self, command_id: str) -> bool: ...
    def list_quick(self) -> list[QuickCommand]: ...
    def get_quick(self, quick_id: str) -> QuickCommand | None: ...
    def save_quick(self, quick: QuickCommand) -> QuickCommand: ...
    def delete_quick(self, quick_id: str) -> bool: ...
    def delete_all(self) -> None: ...


class UnknownCommandCursor(LookupError):
    pass


class InMemoryCommandStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self.commands: dict[str, StoredCommand] = {}
        self.quick: dict[str, tuple[tuple[str, str, str], QuickCommand]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.commands.clear()
            self.quick.clear()

    def bind(self, context: TenantContext) -> "_BoundMemoryStore":
        return _BoundMemoryStore(self, (context.tenant_id, context.workspace_id, context.user_id))


class _BoundMemoryStore:
    def __init__(self, store: InMemoryCommandStore, scope: tuple[str, str, str]) -> None:
        self.store = store
        self.scope = scope

    def _mine(self) -> list[StoredCommand]:
        return [
            command
            for command in self.store.commands.values()
            if (command.tenant_id, command.workspace_id, command.created_by) == self.scope
        ]

    def add_command(self, command: StoredCommand) -> StoredCommand:
        with self.store._lock:
            self.store.commands[command.id] = command
        return command

    def get_command(self, command_id: str) -> StoredCommand | None:
        command = self.store.commands.get(command_id)
        if command is None or (command.tenant_id, command.workspace_id, command.created_by) != self.scope:
            return None
        return command

    def list_commands(self, limit: int, after: str | None) -> list[StoredCommand]:
        with self.store._lock:
            mine = sorted(self._mine(), key=lambda item: (item.created_at, item.id), reverse=True)
        if after:
            cursor = self.get_command(after)
            if cursor is None:
                raise UnknownCommandCursor(after)
            key = (cursor.created_at, cursor.id)
            mine = [item for item in mine if (item.created_at, item.id) < key]
        return mine[:limit]

    def recent_texts(self, limit: int) -> list[tuple[str, str, datetime]]:
        with self.store._lock:
            mine = sorted(self._mine(), key=lambda item: (item.created_at, item.id), reverse=True)
        return [(item.text, item.text_key, item.created_at) for item in mine[:limit]]

    def delete_command(self, command_id: str) -> bool:
        with self.store._lock:
            if self.get_command(command_id) is None:
                return False
            del self.store.commands[command_id]
            return True

    def list_quick(self) -> list[QuickCommand]:
        with self.store._lock:
            items = [quick for scope, quick in self.store.quick.values() if scope == self.scope]
        return sorted(items, key=lambda item: (item.category, item.position, item.created_at, item.id))

    def get_quick(self, quick_id: str) -> QuickCommand | None:
        found = self.store.quick.get(quick_id)
        if found is None or found[0] != self.scope:
            return None
        return found[1]

    def save_quick(self, quick: QuickCommand) -> QuickCommand:
        with self.store._lock:
            self.store.quick[quick.id] = (self.scope, quick)
        return quick

    def delete_quick(self, quick_id: str) -> bool:
        with self.store._lock:
            if self.get_quick(quick_id) is None:
                return False
            del self.store.quick[quick_id]
            return True

    def delete_all(self) -> None:
        """Every command and quick command of this user here ("Delete my data")."""
        with self.store._lock:
            for command in self._mine():
                del self.store.commands[command.id]
            for quick_id in [key for key, (scope, _) in self.store.quick.items() if scope == self.scope]:
                del self.store.quick[quick_id]


memory_command_store = InMemoryCommandStore()


PRIVACY_EXPORT_MAX_COMMANDS = 10_000


def privacy_counts(store: CommandStore) -> dict[str, int]:
    """What "Delete my data" removes of ANUM Command (docs/privacy.md)."""
    return {
        "commands": len(store.recent_texts(PRIVACY_EXPORT_MAX_COMMANDS)),
        "quick_commands": len(store.list_quick()),
    }


def privacy_export(store: CommandStore) -> dict[str, Any]:
    """The caller's command history (with each step's task id) and quick commands."""
    return {
        "commands": [
            command.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "text_key"})
            for command in store.list_commands(PRIVACY_EXPORT_MAX_COMMANDS, None)
        ],
        "quick_commands": [item.model_dump(mode="json") for item in store.list_quick()],
    }


def command_store(repository: AnumRepository, context: TenantContext) -> CommandStore:
    """The caller's commands in the request's unit of work (the same transaction as the
    tasks a command creates). With PostgreSQL ``anum.user_id`` is set on that transaction,
    so the rows' RLS policy admits only the caller's own."""
    if settings.repository_backend == "memory":
        return memory_command_store.bind(context)
    from .db.command_repository import SqlAlchemyCommandStore

    return SqlAlchemyCommandStore(getattr(repository, "session"), context)


# -- views -------------------------------------------------------------------------------


class CommandStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandStepView(BaseModel):
    position: int
    title: str
    instruction: str
    agent: CommandAgent
    tool: str | None
    risk_level: RiskLevel
    approval: ApprovalNeed
    task_id: str
    # Null when the task can no longer be read (it was deleted with the workspace's data).
    task: Task | None
    status: str
    progress: float
    run_id: str | None = None
    result: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    approvals: list[Approval] = Field(default_factory=list)


class TimelineEntry(BaseModel):
    id: str
    at: datetime
    type: str
    task_id: str
    position: int
    agent: CommandAgent
    title: str


class CommandFile(BaseModel):
    kind: Literal["attachment", "run_log"]
    name: str
    # API path to download the file (with the caller's own headers).
    path: str
    file_id: str | None = None
    task_id: str | None = None
    content_type: str | None = None


class CommandSummary(BaseModel):
    id: str
    text: str
    source: CommandSource
    locale: Locale
    status: CommandStatus
    progress: float
    step_count: int
    completed_steps: int
    pending_approvals: int
    agents: list[CommandAgent]
    created_at: datetime
    updated_at: datetime


class CommandView(CommandSummary):
    created_by: str
    quick_command_id: str | None = None
    steps: list[CommandStepView]
    pending: list[Approval]
    timeline: list[TimelineEntry]
    files: list[CommandFile]
    attachments: list[CommandAttachment]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
_PHASE_PROGRESS = {
    RunPhase.PLANNING: 0.3,
    RunPhase.TOOL_READY: 0.5,
    RunPhase.WAITING_APPROVAL: 0.6,
    RunPhase.EXECUTING: 0.8,
}


def _step_progress(task: Task | None, run: AgentRun | None) -> float:
    if task is None or task.status in _TERMINAL:
        return 1.0
    if task.status == TaskStatus.CREATED:
        return 0.0
    if task.status == TaskStatus.QUEUED:
        return 0.1
    if run is None:
        return 0.2
    return _PHASE_PROGRESS.get(run.checkpoint.phase, 0.5)


def derive_status(statuses: list[str]) -> CommandStatus:
    values = set(statuses)
    if not values:
        return CommandStatus.READY
    if values & {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        return CommandStatus.RUNNING
    if TaskStatus.WAITING_APPROVAL in values:
        return CommandStatus.WAITING_APPROVAL
    if values == {TaskStatus.CREATED}:
        return CommandStatus.READY
    if TaskStatus.CREATED in values:
        return CommandStatus.RUNNING
    if values == {TaskStatus.COMPLETED}:
        return CommandStatus.COMPLETED
    if values == {TaskStatus.CANCELLED}:
        return CommandStatus.CANCELLED
    if TaskStatus.COMPLETED in values:
        return CommandStatus.PARTIAL
    return CommandStatus.FAILED


class _StepState(BaseModel):
    step: StoredStep
    task: Task | None
    run: AgentRun | None
    approvals: list[Approval]


def _states(command: StoredCommand, repository: AnumRepository, context: TenantContext) -> list[_StepState]:
    now = utc_now()
    states: list[_StepState] = []
    for step in sorted(command.steps, key=lambda item: item.position):
        task = repository.get_task(step.task_id, context)
        run = repository.find_run_for_task(step.task_id, context) if task is not None else None
        approvals = (
            [as_viewed(approval, now) for approval in repository.list_task_approvals(step.task_id, context)]
            if task is not None
            else []
        )
        states.append(_StepState(step=step, task=task, run=run, approvals=approvals))
    return states


def _summary(command: StoredCommand, states: list[_StepState]) -> CommandSummary:
    statuses = [state.task.status.value if state.task else TaskStatus.FAILED.value for state in states]
    progress = [_step_progress(state.task, state.run) for state in states]
    agents: dict[AgentId, CommandAgent] = {}
    for step in command.steps:
        agents.setdefault(step.agent, agent_view(step.agent, command.locale))
    return CommandSummary(
        id=command.id,
        text=command.text,
        source=command.source,
        locale=command.locale,
        status=derive_status(statuses),
        progress=round(sum(progress) / len(progress), 3) if progress else 0.0,
        step_count=len(states),
        completed_steps=sum(1 for value in statuses if value == TaskStatus.COMPLETED),
        pending_approvals=sum(
            1 for state in states for approval in state.approvals if approval.status == ApprovalStatus.PENDING
        ),
        agents=list(agents.values()),
        created_at=command.created_at,
        updated_at=command.updated_at,
    )


def build_view(command: StoredCommand, repository: AnumRepository, context: TenantContext) -> CommandView:
    states = _states(command, repository, context)
    summary = _summary(command, states)
    steps: list[CommandStepView] = []
    timeline: list[TimelineEntry] = []
    files = [
        CommandFile(
            kind="attachment",
            name=attachment.name,
            path=f"/api/v1/files/{attachment.file_id}/content",
            file_id=attachment.file_id,
            content_type=attachment.content_type,
        )
        for attachment in command.attachments
    ]
    started: list[datetime] = []
    finished: list[datetime] = []
    for state in states:
        step, task, run = state.step, state.task, state.run
        agent = agent_view(step.agent, command.locale)
        done = task is not None and task.status in _TERMINAL
        if run is not None:
            started.append(run.created_at)
        if done and task is not None:
            finished.append(run.updated_at if run is not None else task.updated_at)
        steps.append(
            CommandStepView(
                position=step.position,
                title=step.title,
                instruction=step.instruction,
                agent=agent,
                tool=step.tool,
                risk_level=step.risk_level,
                approval=step.approval,
                task_id=step.task_id,
                task=task,
                status=task.status.value if task is not None else "missing",
                progress=_step_progress(task, run),
                run_id=run.id if run is not None else None,
                result=run.result if run is not None else None,
                started_at=run.created_at if run is not None else None,
                finished_at=(run.updated_at if run is not None else None) if done else None,
                approvals=state.approvals,
            )
        )
        if task is None:
            continue
        events = repository.list_task_events(step.task_id, context, limit=TIMELINE_EVENTS_PER_TASK)
        timeline.extend(_timeline_entry(event, step, agent) for event in events)
        if run is not None:
            files.append(
                CommandFile(
                    kind="run_log",
                    name=f"{step.position:02d}-{_safe_name(step.title)}-run-log.md",
                    path=f"/api/v1/tasks/{step.task_id}/export?format=markdown",
                    task_id=step.task_id,
                    content_type="text/markdown",
                )
            )
    timeline.sort(key=lambda entry: (entry.at, entry.position, entry.id))
    start = min(started) if started else None
    end = max(finished) if finished and summary.status not in {
        CommandStatus.RUNNING,
        CommandStatus.WAITING_APPROVAL,
        CommandStatus.READY,
    } else None
    return CommandView(
        **summary.model_dump(),
        created_by=command.created_by,
        quick_command_id=command.quick_command_id,
        steps=steps,
        pending=[
            approval
            for state in states
            for approval in state.approvals
            if approval.status == ApprovalStatus.PENDING
        ],
        timeline=timeline,
        files=files,
        attachments=command.attachments,
        started_at=start,
        finished_at=end,
        duration_seconds=(end - start).total_seconds() if start and end else None,
    )


def _timeline_entry(event: DomainEvent, step: StoredStep, agent: CommandAgent) -> TimelineEntry:
    return TimelineEntry(
        id=event.id,
        at=event.created_at,
        type=event.type,
        task_id=step.task_id,
        position=step.position,
        agent=agent,
        title=step.title,
    )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w-]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    return cleaned[:40] or "step"


# -- routes: plan and create -------------------------------------------------------------


def _audit(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    target: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="succeeded",
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


@router.post("/commands/preview", response_model=CommandPlan)
async def preview_command(
    payload: CommandPlanRequest,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CommandPlan:
    """The plan a request would run: steps, agents, tools, risk and approvals. Read-only."""
    require_permission(context, Permission.TASK_CREATE)
    locale = payload.locale or language_of(payload.text)
    return Planner(_tools(request), repository, context, locale).plan(payload.text)


class CommandStepInput(BaseModel):
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION_CHARS)

    @field_validator("instruction")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class CommandCreate(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    # The confirmed plan: the steps to run, in order, after any edit, removal or reorder.
    steps: list[CommandStepInput] = Field(min_length=1, max_length=MAX_STEPS)
    source: CommandSource = CommandSource.TYPED
    locale: Locale | None = None
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    quick_command_id: str | None = Field(default=None, max_length=80)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("attachments")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item or len(item) > 80 for item in value):
            raise ValueError("attachments must be distinct file ids")
        return value


def _attachments(file_ids: list[str], context: TenantContext) -> list[CommandAttachment]:
    if not file_ids:
        return []
    require_permission(context, Permission.FILE_READ)
    from .files import open_file_metadata_store

    found: list[CommandAttachment] = []
    with open_file_metadata_store(context) as files:
        for file_id in file_ids:
            record = files.get(context, file_id)
            if record is None:
                raise HTTPException(422, f"Attached file not found: {file_id}")
            found.append(CommandAttachment(file_id=record.id, name=record.name, content_type=record.content_type))
    return found


@router.post("/commands", response_model=CommandView, status_code=status.HTTP_201_CREATED)
async def create_command(
    payload: CommandCreate,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CommandView:
    """Create the command and one task per confirmed step; start them with ``/tasks/{id}/run``."""
    return create_command_record(payload, _tools(request), repository, context)


def create_command_record(
    payload: CommandCreate, tools: ToolRegistry, repository: AnumRepository, context: TenantContext
) -> CommandView:
    """``POST /commands`` in the caller's unit of work: the route and a quick command run by
    voice (voice_actions.py) plan, refuse blocked steps, create and audit the same way."""
    require_permission(context, Permission.TASK_CREATE)
    locale = payload.locale or language_of(payload.text)
    planner = Planner(tools, repository, context, locale)
    planned = [planner.step(index, step.instruction) for index, step in enumerate(payload.steps, 1)]
    blocked = [step.position for step in planned if step.approval == ApprovalNeed.BLOCKED]
    if blocked:
        raise HTTPException(
            422,
            f"Step {blocked[0]} is blocked by policy: {planned[blocked[0] - 1].approval_reason} Remove it and try again.",
        )
    store = command_store(repository, context)
    quick = None
    if payload.quick_command_id is not None:
        quick = store.get_quick(payload.quick_command_id)
        if quick is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Quick command not found")
    attachments = _attachments(payload.attachments, context)
    now = utc_now()
    command_id = new_id("command")
    stored_steps: list[StoredStep] = []
    for step in planned:
        task = Task(
            id=new_id("task"),
            title=step.title[:160],
            prompt=step.instruction,
            status=TaskStatus.CREATED,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            created_at=now,
            updated_at=now,
            created_by=context.user_id,
        )
        repository.create_task(task)
        repository.record_event(
            create_event(
                CanonicalEventName.TASK_CREATED,
                context,
                task.id,
                {"title": task.title, "command_id": command_id, "position": step.position},
                correlation_id=task.id,
                created_at=now,
            ).event
        )
        stored_steps.append(
            StoredStep(
                position=step.position,
                task_id=task.id,
                title=step.title,
                instruction=step.instruction,
                agent=step.agent.id,
                tool=step.tool.name if step.tool else None,
                risk_level=step.risk_level,
                approval=step.approval,
            )
        )
    command = store.add_command(
        StoredCommand(
            id=command_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            created_by=context.user_id,
            text=payload.text,
            text_key=text_key(payload.text),
            source=payload.source,
            locale=locale,
            steps=stored_steps,
            attachments=attachments,
            quick_command_id=quick.id if quick else None,
            created_at=now,
            updated_at=now,
        )
    )
    if quick is not None:
        store.save_quick(quick.model_copy(update={"use_count": quick.use_count + 1, "last_used_at": now}))
    # Counts and kinds only: never the request text (docs/command.md#audit).
    _audit(
        repository,
        context,
        "command.created",
        command.id,
        {
            "steps": len(stored_steps),
            "source": payload.source.value,
            "agents": sorted({step.agent.value for step in stored_steps}),
            "approvals_required": sum(1 for step in stored_steps if step.approval == ApprovalNeed.REQUIRED),
            "attachments": len(attachments),
        },
    )
    return build_view(command, repository, context)


# -- routes: read ------------------------------------------------------------------------


def _command_or_404(store: CommandStore, command_id: str) -> StoredCommand:
    command = store.get_command(command_id)
    if command is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
    return command


@router.get("/commands", response_model=list[CommandSummary])
async def list_commands(
    limit: int = Query(default=COMMAND_PAGE_DEFAULT, ge=1, le=COMMAND_PAGE_MAX),
    cursor: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[CommandSummary]:
    """The caller's own commands, newest first; ``cursor`` is the last id of the previous page."""
    require_permission(context, Permission.TASK_READ)
    store = command_store(repository, context)
    try:
        commands = store.list_commands(limit, cursor or None)
    except UnknownCommandCursor:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown or invalid cursor") from None
    return [_summary(command, _states(command, repository, context)) for command in commands]


class CommandSuggestion(BaseModel):
    kind: Literal["quick", "recent", "starter"]
    text: str
    title: str
    quick_command_id: str | None = None
    use_count: int = 0
    last_used_at: datetime | None = None


_STARTERS: list[tuple[str | None, str, str]] = [
    # (tool that must be registered, English, Arabic)
    ("search_email", "Check my emails and summarize the urgent ones", "تحقق من بريدي ولخص الرسائل العاجلة"),
    ("list_whatsapp_conversations", "Which WhatsApp chats are waiting for a reply?", "ما محادثات واتساب التي تنتظر ردا؟"),
    ("web_search", "Search the web for tyre prices in Saudi Arabia", "ابحث في الإنترنت عن أسعار الإطارات في السعودية"),
    (None, "Prepare a summary of my latest notes and files", "جهز ملخصا لأحدث ملاحظاتي وملفاتي"),
    (None, "Draft a meeting agenda for tomorrow with the team", "جهز مسودة جدول أعمال لاجتماع الغد مع الفريق"),
]


@router.get("/commands/suggestions", response_model=list[CommandSuggestion])
async def command_suggestions(
    request: Request,
    locale: Locale = "en",
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[CommandSuggestion]:
    """Chips for the Command screen: the caller's quick commands, then their most used and
    most recent commands, then starters for what this deployment can do."""
    require_permission(context, Permission.TASK_READ)
    store = command_store(repository, context)
    items: list[CommandSuggestion] = []
    seen: set[str] = set()
    quick = sorted(store.list_quick(), key=lambda item: (-item.use_count, item.position, item.created_at))
    for item in quick[:4]:
        seen.add(text_key(item.text))
        items.append(
            CommandSuggestion(
                kind="quick",
                text=item.text,
                title=item.title,
                quick_command_id=item.id,
                use_count=item.use_count,
                last_used_at=item.last_used_at,
            )
        )
    recent = store.recent_texts(200)
    counts = Counter(key for _, key, _ in recent)
    latest: dict[str, tuple[str, datetime]] = {}
    for text, key, created_at in recent:  # newest first: the first text seen is the latest
        latest.setdefault(key, (text, created_at))
    ranked = sorted(latest, key=lambda key: (-counts[key], -latest[key][1].timestamp()))
    for key in ranked:
        if len(items) >= SUGGESTION_LIMIT - 2 or key in seen:
            continue
        seen.add(key)
        text, created_at = latest[key]
        items.append(
            CommandSuggestion(kind="recent", text=text, title=_title(text), use_count=counts[key], last_used_at=created_at)
        )
    tools = _tools(request).names
    for tool, english, arabic in _STARTERS:
        if len(items) >= SUGGESTION_LIMIT:
            break
        text = arabic if locale == "ar" else english
        if (tool is not None and tool not in tools) or text_key(text) in seen:
            continue
        seen.add(text_key(text))
        items.append(CommandSuggestion(kind="starter", text=text, title=_title(text)))
    return items


@router.get("/commands/{command_id}", response_model=CommandView)
async def get_command(
    command_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CommandView:
    """The live view of one of the caller's commands."""
    require_permission(context, Permission.TASK_READ)
    command = _command_or_404(command_store(repository, context), command_id)
    return build_view(command, repository, context)


@router.delete("/commands/{command_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_command(
    command_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Remove a command from the caller's history; its tasks stay in the workspace."""
    require_permission(context, Permission.TASK_READ)
    store = command_store(repository, context)
    command = _command_or_404(store, command_id)
    states = _states(command, repository, context)
    if derive_status([state.task.status.value for state in states if state.task]) in {
        CommandStatus.RUNNING,
        CommandStatus.WAITING_APPROVAL,
    }:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stop the command or let it finish before removing it")
    store.delete_command(command_id)
    _audit(repository, context, "command.deleted", command_id, {"steps": len(command.steps)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def report_markdown(view: CommandView) -> str:
    lines = [f"# {view.text}", "", f"- Status: {view.status.value}", f"- Created: {view.created_at.isoformat()}"]
    if view.duration_seconds is not None:
        lines.append(f"- Duration: {round(view.duration_seconds)} s")
    lines.append(f"- Steps: {view.completed_steps} of {view.step_count} completed")
    for step in view.steps:
        lines += ["", f"## {step.position}. {step.title}", "", f"- Agent: {step.agent.name}"]
        lines.append(f"- Status: {step.status}")
        if step.tool:
            lines.append(f"- Tool: {step.tool} (risk {step.risk_level.value})")
        for approval in step.approvals:
            decided = f" by {approval.decided_by}" if approval.decided_by else ""
            lines.append(f"- Approval {approval.action}: {approval.status.value}{decided}")
        lines += ["", step.result.strip() if step.result else "_No result yet._"]
    if view.attachments:
        lines += ["", "## Attachments", ""] + [f"- {item.name}" for item in view.attachments]
    return "\n".join(lines) + "\n"


@router.get("/commands/{command_id}/report")
async def command_report(
    command_id: str,
    report_format: str = Query(default="json", alias="format", pattern="^(json|markdown)$"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """The full report of a command: every step's result, approvals and attachments."""
    require_permission(context, Permission.TASK_READ)
    command = _command_or_404(command_store(repository, context), command_id)
    view = build_view(command, repository, context)
    _audit(repository, context, "command.exported", command_id, {"format": report_format})
    if report_format == "markdown":
        return Response(
            content=report_markdown(view),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{_safe_name(command.id)}-report.md"',
                "Cache-Control": "no-store",
            },
        )
    return Response(
        content=view.model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


class NextStep(BaseModel):
    id: str
    kind: Literal["command", "review_approvals", "save_quick_command"]
    label: str
    # The request a ``command`` step starts (preview it first), or the text to save.
    text: str | None = None


_AGENT_NEXT: dict[AgentId, list[tuple[str, str]]] = {
    AgentId.EMAIL: [
        ("Draft replies to the most urgent emails", "جهز ردودا على أهم الرسائل العاجلة"),
        ("Send the summary to the team", "أرسل الملخص إلى الفريق"),
    ],
    AgentId.WHATSAPP: [("Draft replies to the customers waiting on WhatsApp", "جهز ردودا للعملاء المنتظرين على واتساب")],
    AgentId.COMMERCE: [
        ("Show the products that are low on stock", "اعرض المنتجات منخفضة المخزون"),
        ("Summarize this week's sales by channel", "لخص مبيعات هذا الأسبوع حسب القناة"),
    ],
    AgentId.TYRE: [
        ("Create maintenance tasks for the high-cost vehicles", "أنشئ مهام صيانة للمركبات عالية التكلفة"),
        ("Compare tyre costs with last month", "قارن تكاليف الإطارات بالشهر الماضي"),
    ],
    AgentId.CALENDAR: [("Draft the agenda and send it to the attendees", "جهز جدول الأعمال وأرسله إلى الحضور")],
    AgentId.WRITER: [("Turn this summary into an email for the team", "حوّل هذا الملخص إلى رسالة للفريق")],
    AgentId.RESEARCH: [("Search for more recent sources on this", "ابحث عن مصادر أحدث حول هذا الموضوع")],
    AgentId.ACTION: [],
    AgentId.GENERAL: [("Prepare a short report of these results", "جهز تقريرا قصيرا بهذه النتائج")],
}


def next_steps(view: CommandView, quick_texts: set[str]) -> list[NextStep]:
    """Suggested next actions derived from the command's results (no model call)."""
    arabic = view.locale == "ar"
    items: list[NextStep] = []
    if view.pending:
        count = len(view.pending)
        items.append(
            NextStep(
                id="review_approvals",
                kind="review_approvals",
                label=f"راجع {count} من الموافقات المعلقة" if arabic else f"Review {count} pending approval{'s' if count != 1 else ''}",
            )
        )
    for step in view.steps:
        if step.status in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            items.append(
                NextStep(
                    id=f"retry_{step.position}",
                    kind="command",
                    label=f"أعد المحاولة: {step.title}" if arabic else f"Try again: {step.title}",
                    text=step.instruction,
                )
            )
    seen: set[AgentId] = set()
    for step in view.steps:
        if step.status != TaskStatus.COMPLETED.value or step.agent.id in seen:
            continue
        seen.add(step.agent.id)
        for index, (english, arabic_text) in enumerate(_AGENT_NEXT[step.agent.id]):
            text = arabic_text if arabic else english
            items.append(NextStep(id=f"{step.agent.id.value}_{index}", kind="command", label=text, text=text))
    if view.status == CommandStatus.COMPLETED and text_key(view.text) not in quick_texts:
        items.append(
            NextStep(
                id="save_quick_command",
                kind="save_quick_command",
                label="احفظ هذا الأمر كأمر سريع" if arabic else "Save this as a quick command",
                text=view.text,
            )
        )
    return items[:NEXT_STEP_LIMIT]


@router.get("/commands/{command_id}/next-steps", response_model=list[NextStep])
async def command_next_steps(
    command_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[NextStep]:
    require_permission(context, Permission.TASK_READ)
    store = command_store(repository, context)
    command = _command_or_404(store, command_id)
    view = build_view(command, repository, context)
    return next_steps(view, {text_key(item.text) for item in store.list_quick()})


# -- routes: quick commands --------------------------------------------------------------


class QuickCommandCreate(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    text: str = Field(min_length=1, max_length=2000)
    category: QuickCommandCategory = QuickCommandCategory.CUSTOM
    position: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("title", "text")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class QuickCommandUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_CHARS)
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    category: QuickCommandCategory | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("title", "text")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _something(self) -> "QuickCommandUpdate":
        if self.title is None and self.text is None and self.category is None and self.position is None:
            raise ValueError("send a new title, text, category or position")
        return self


@router.get("/quick-commands", response_model=list[QuickCommand])
async def list_quick_commands(
    category: QuickCommandCategory | None = None,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[QuickCommand]:
    """The caller's quick commands, by category and position."""
    require_permission(context, Permission.TASK_READ)
    items = command_store(repository, context).list_quick()
    return [item for item in items if category is None or item.category == category]


@router.post("/quick-commands", response_model=QuickCommand, status_code=status.HTTP_201_CREATED)
async def create_quick_command(
    payload: QuickCommandCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> QuickCommand:
    require_permission(context, Permission.TASK_CREATE)
    store = command_store(repository, context)
    existing = store.list_quick()
    if len(existing) >= MAX_QUICK_COMMANDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"You can keep up to {MAX_QUICK_COMMANDS} quick commands; delete one first"
        )
    same_category = [item.position for item in existing if item.category == payload.category]
    now = utc_now()
    quick = store.save_quick(
        QuickCommand(
            id=new_id("quick"),
            title=payload.title,
            text=payload.text,
            category=payload.category,
            position=payload.position if payload.position is not None else (max(same_category, default=-1) + 1),
            created_at=now,
            updated_at=now,
        )
    )
    _audit(repository, context, "quick_command.created", quick.id, {"category": quick.category.value})
    return quick


def _quick_or_404(store: CommandStore, quick_id: str) -> QuickCommand:
    quick = store.get_quick(quick_id)
    if quick is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quick command not found")
    return quick


@router.put("/quick-commands/{quick_id}", response_model=QuickCommand)
async def update_quick_command(
    quick_id: str,
    payload: QuickCommandUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> QuickCommand:
    require_permission(context, Permission.TASK_CREATE)
    store = command_store(repository, context)
    quick = _quick_or_404(store, quick_id)
    changes = payload.model_dump(exclude_none=True)
    updated = store.save_quick(quick.model_copy(update={**changes, "updated_at": utc_now()}))
    _audit(repository, context, "quick_command.updated", quick_id, {"fields": sorted(changes)})
    return updated


@router.delete("/quick-commands/{quick_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_quick_command(
    quick_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    require_permission(context, Permission.TASK_CREATE)
    store = command_store(repository, context)
    _quick_or_404(store, quick_id)
    store.delete_quick(quick_id)
    _audit(repository, context, "quick_command.deleted", quick_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
