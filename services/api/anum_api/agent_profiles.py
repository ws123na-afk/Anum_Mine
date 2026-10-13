"""User-built agents: agent profiles, their memories and their templates (docs/agents.md).

An agent profile is a named configuration a person builds in a workspace: who the agent
is (name, icon, color, category, optional department, description), how it works
(instructions, model route), what it may use (skills and tools, an autonomy level per
tool category, knowledge sources) and who can see it (private to its owner, or shared
with the workspace). Department assistants are the same model (``category=work`` with a
``department``): there is one agent-profile model.

Nothing on a profile can widen what the runtime allows:

- ``allowed_tools`` is checked against the tools registered in this deployment and the
  roles of the person saving the profile (a tool their role could not use is refused);
  at run time the profile's tools are an allowlist *inside* the runtime's tool policy.
- ``allowed_skills`` must be built-in runtime skills whose tools exist, or skills
  installed and enabled in the workspace; a built-in skill needs its tools allowed.
- ``autonomy`` can only make a call stricter: ``off`` blocks a category, ``ask`` turns an
  allowed call into an approval, ``auto`` leaves the platform decision as it is. High
  risk ("external") actions always need approval, so ``external`` may not be ``auto``;
  workspace and organization approval rules still apply on top.

Storage: in process memory with ``ANUM_REPOSITORY_BACKEND=memory``; with ``postgresql``
in ``agent_profiles`` and ``agent_memories`` (migration ``0041_agent_profiles``) under
forced RLS on the tenant, the workspace and, for private agents, the owner's user id.
Every change is audited (``agent.created``, ``agent.updated``, ``agent.deleted``,
``agent_memory.created``, ``agent_memory.deleted``) without instruction or memory text.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent_skills import SkillRegistry, default_skill_registry
from .agent_tools import ToolDefinition, ToolRegistry, default_tool_registry
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store

MAX_AGENTS_PER_WORKSPACE = 200
MAX_AGENT_MEMORIES = 500
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 50
NAME_CHARS = 80
DESCRIPTION_CHARS = 500
INSTRUCTIONS_CHARS = 8000
MEMORY_CHARS = 2000
# The internal text-response tool has no side effects: every agent may answer in text.
ALWAYS_ALLOWED_TOOL = "anum.respond"
# Planning itself is not a capability to grant (no tools, no triggers).
ALWAYS_SELECTED_SKILL = "anum.task-planning"

AGENT_ICONS = (
    "bot", "truck", "search", "mail", "calendar", "file-text", "phone",
    "briefcase", "user", "sparkles", "wrench", "chart", "shopping-cart", "heart",
)
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class AgentCategory(StrEnum):
    WORK = "work"
    PERSONAL = "personal"
    CUSTOM = "custom"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class AgentSharing(StrEnum):
    PRIVATE = "private"
    WORKSPACE = "workspace"


class AgentModelRoute(StrEnum):
    FAST = "fast"
    CAPABLE = "capable"


class AutonomyLevel(StrEnum):
    OFF = "off"
    ASK = "ask"
    AUTO = "auto"


class ToolCategory(StrEnum):
    """What a tool does, from its risk level: reading, changing ANUM, or acting outside it."""

    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


class KnowledgeMode(StrEnum):
    NONE = "none"
    WORKSPACE = "workspace"
    SELECTED = "selected"


def tool_category(risk_level: RiskLevel) -> ToolCategory:
    if risk_level == RiskLevel.LOW:
        return ToolCategory.READ
    if risk_level == RiskLevel.MEDIUM:
        return ToolCategory.WRITE
    return ToolCategory.EXTERNAL


_AUTONOMY_ORDER = {AutonomyLevel.OFF: 0, AutonomyLevel.ASK: 1, AutonomyLevel.AUTO: 2}
_CATEGORY_ORDER = {ToolCategory.READ: 0, ToolCategory.WRITE: 1, ToolCategory.EXTERNAL: 2}
_WRITE_KINDS = frozenset({"draft", "create", "update", "archive", "pause"})


def agent_tool_category(name: str, definition: ToolDefinition | None) -> ToolCategory:
    """The agent category of a tool: the stricter of its risk and its autonomy action kind.

    The kind is the tool's declared ``action_kind`` (docs/autonomy.md), else autonomy's
    cautious guess from the name. ``read`` is reading; drafts, creates, updates, archives
    and pauses change things inside ANUM; replies, sends, deletes, money, price and stock
    changes, merges and anything unknown act outside it.
    """
    from .autonomy import ActionKind, kind_of

    by_risk = tool_category(definition.risk_level if definition is not None else RiskLevel.HIGH)
    kind = kind_of(name, definition)
    if kind == ActionKind.READ:
        by_kind = ToolCategory.READ
    elif kind.value in _WRITE_KINDS:
        by_kind = ToolCategory.WRITE
    else:
        by_kind = ToolCategory.EXTERNAL
    return max(by_risk, by_kind, key=_CATEGORY_ORDER.__getitem__)


class AgentAutonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: AutonomyLevel = AutonomyLevel.AUTO
    write: AutonomyLevel = AutonomyLevel.ASK
    external: AutonomyLevel = AutonomyLevel.ASK

    @field_validator("external")
    @classmethod
    def _external_needs_approval(cls, value: AutonomyLevel) -> AutonomyLevel:
        if value == AutonomyLevel.AUTO:
            raise ValueError("Actions outside ANUM always need approval: choose ask or off")
        return value

    def level(self, category: ToolCategory) -> AutonomyLevel:
        return AutonomyLevel(getattr(self, category.value))


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["memory", "file"]
    id: str = Field(min_length=1, max_length=80)


class AgentProfileFields(BaseModel):
    """What a person sets on an agent (create, and every field of an update)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=NAME_CHARS)
    icon: str = Field(default="bot", max_length=40)
    color: str = Field(default="#4f46e5", pattern=COLOR_PATTERN)
    category: AgentCategory = AgentCategory.CUSTOM
    department: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=DESCRIPTION_CHARS)
    instructions: str = Field(default="", max_length=INSTRUCTIONS_CHARS)
    model_route: AgentModelRoute = AgentModelRoute.CAPABLE
    allowed_skills: list[str] = Field(default_factory=list, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    autonomy: AgentAutonomy = Field(default_factory=AgentAutonomy)
    knowledge_mode: KnowledgeMode = KnowledgeMode.NONE
    knowledge_sources: list[KnowledgeSource] = Field(default_factory=list, max_length=50)
    sharing: AgentSharing = AgentSharing.PRIVATE
    template_id: str | None = Field(default=None, max_length=40)

    @field_validator("icon")
    @classmethod
    def _known_icon(cls, value: str) -> str:
        if value not in AGENT_ICONS:
            raise ValueError(f"Unknown icon; use one of: {', '.join(AGENT_ICONS)}")
        return value

    @field_validator("department")
    @classmethod
    def _blank_department(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("allowed_skills", "allowed_tools")
    @classmethod
    def _unique_names(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _knowledge_matches_mode(self) -> "AgentProfileFields":
        if self.knowledge_mode != KnowledgeMode.SELECTED:
            self.knowledge_sources = []
        elif not self.knowledge_sources:
            raise ValueError("Pick at least one memory or file, or choose another knowledge setting")
        seen: set[tuple[str, str]] = set()
        unique = []
        for source in self.knowledge_sources:
            if (source.type, source.id) not in seen:
                seen.add((source.type, source.id))
                unique.append(source)
        self.knowledge_sources = unique
        return self


class AgentProfileCreate(AgentProfileFields):
    pass


class AgentProfileUpdate(BaseModel):
    """Only the fields sent change. ``version`` (optional) refuses a stale edit with 409."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=NAME_CHARS)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    category: AgentCategory | None = None
    department: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=DESCRIPTION_CHARS)
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_CHARS)
    model_route: AgentModelRoute | None = None
    allowed_skills: list[str] | None = Field(default=None, max_length=50)
    allowed_tools: list[str] | None = Field(default=None, max_length=50)
    autonomy: AgentAutonomy | None = None
    knowledge_mode: KnowledgeMode | None = None
    knowledge_sources: list[KnowledgeSource] | None = Field(default=None, max_length=50)
    sharing: AgentSharing | None = None
    status: AgentStatus | None = None
    version: int | None = Field(default=None, ge=1)


class AgentProfile(AgentProfileFields):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    status: AgentStatus = AgentStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    version: int = 1


class AgentProfileView(AgentProfile):
    """A profile as the caller sees it, with what they may do and what is unavailable now."""

    can_edit: bool
    can_run: bool
    # Allowed tools or skills that this deployment or workspace no longer provides.
    unavailable_tools: list[str] = Field(default_factory=list)
    unavailable_skills: list[str] = Field(default_factory=list)


class AgentPage(BaseModel):
    items: list[AgentProfileView]
    next_cursor: str | None = None
    counts: dict[str, int] = Field(description="Agents the caller can see, per category, and `all`")


class AgentMemorySource(StrEnum):
    MANUAL = "manual"
    TASK = "task"


class AgentMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=MEMORY_CHARS)
    task_id: str | None = Field(default=None, max_length=80)


class AgentMemory(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    agent_id: str
    content: str
    source: AgentMemorySource
    task_id: str | None = None
    created_by: str
    created_at: datetime


class AgentMemoryPage(BaseModel):
    items: list[AgentMemory]
    next_cursor: str | None = None
    total: int


class AgentCatalogTool(BaseModel):
    name: str
    description: str
    risk_level: RiskLevel
    category: ToolCategory
    always_on: bool = False
    available_to_you: bool
    reason: str | None = None


class AgentCatalogSkill(BaseModel):
    id: str
    name: str
    description: str
    source: Literal["builtin", "installed"]
    required_tools: list[str]


class AgentTemplate(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    category: AgentCategory
    description: str
    instructions: str
    model_route: AgentModelRoute
    allowed_tools: list[str]
    allowed_skills: list[str]
    autonomy: AgentAutonomy
    knowledge_mode: KnowledgeMode
    # What the template would use that this deployment does not have yet.
    missing_capabilities: list[str] = Field(default_factory=list)


class AgentCatalog(BaseModel):
    tools: list[AgentCatalogTool]
    skills: list[AgentCatalogSkill]
    templates: list[AgentTemplate]
    icons: list[str]
    autonomy_floor: AgentAutonomy = Field(
        description="The least restrictive level each category may run at in this workspace"
    )


# --------------------------------------------------------------------------- templates


class _TemplateSpec(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    category: AgentCategory
    description: str
    instructions: str
    model_route: AgentModelRoute = AgentModelRoute.CAPABLE
    tool_patterns: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    knowledge_mode: KnowledgeMode = KnowledgeMode.NONE
    wants: list[tuple[str, str]] = Field(default_factory=list)  # (tool pattern, capability shown when missing)


TEMPLATES: tuple[_TemplateSpec, ...] = (
    _TemplateSpec(
        id="fleet",
        name="Fleet Agent",
        icon="truck",
        color="#0f766e",
        category=AgentCategory.WORK,
        description="Tyres, vehicles and maintenance: plans rotations, checks wear and schedules service.",
        instructions=(
            "You help plan tyre and vehicle maintenance for the fleet. Use tyre-planning and fleet "
            "tools when they are available; otherwise give a clear plan the user can act on. Always "
            "state the vehicle, the tyre position and the reason for each recommendation."
        ),
        # The tyre-planning tools (docs/tyre-planning.md) once the fleet data is connected.
        tool_patterns=[
            "tyre_*", "tyres_*", "purchase_plan", "tyre*", "tyrepulse.*", "fleet.*", "vehicle.*", "maintenance.*",
        ],
        skills=["anum.tyre-planning", "anum.document-drafting"],
        knowledge_mode=KnowledgeMode.WORKSPACE,
        wants=[("tyre*", "Tyre-planning tools (TyrePulse connector)")],
    ),
    _TemplateSpec(
        id="research",
        name="Research Agent",
        icon="search",
        color="#2563eb",
        category=AgentCategory.WORK,
        description="Looks things up on the web and summarizes what it found, with sources.",
        instructions=(
            "Research the question on the web, compare sources and answer with a short summary "
            "followed by the sources you used. Say clearly when sources disagree or are missing."
        ),
        tool_patterns=["web_search"],
        skills=["anum.web-search", "anum.document-drafting"],
        wants=[("web_search", "Web search (set ANUM_SEARXNG_URL)")],
    ),
    _TemplateSpec(
        id="email",
        name="Email Agent",
        icon="mail",
        color="#db2777",
        category=AgentCategory.WORK,
        description="Drafts and summarizes email. Sending needs an email connector and your approval.",
        instructions=(
            "Draft clear, polite emails in the user's voice and summarize long threads into actions. "
            "Never send anything without the user's approval."
        ),
        model_route=AgentModelRoute.FAST,
        # The email inbox tools (docs/email.md) once a mailbox is connected; sending is
        # high risk, so it always waits for approval.
        tool_patterns=[
            "search_email", "read_email", "draft_reply", "send_email", "mark_email_read",
            "archive_email", "unarchive_email", "email.*", "gmail.*", "outlook.*", "zoho_mail.*",
        ],
        skills=["anum.email", "anum.document-drafting"],
        wants=[("*_email", "An email connector (Gmail, Outlook or Zoho Mail)")],
    ),
    _TemplateSpec(
        id="calendar",
        name="Calendar Agent",
        icon="calendar",
        color="#ea580c",
        category=AgentCategory.PERSONAL,
        description="Plans your schedule and drafts meeting notes. Booking needs a calendar connector.",
        instructions=(
            "Help plan the user's time: propose schedules, prepare agendas and summarize meetings. "
            "Never book or change events without approval."
        ),
        model_route=AgentModelRoute.FAST,
        tool_patterns=["calendar.*"],
        skills=["anum.document-drafting"],
        wants=[("calendar.*", "A calendar connector")],
    ),
    _TemplateSpec(
        id="document",
        name="Document Agent",
        icon="file-text",
        color="#7c3aed",
        category=AgentCategory.WORK,
        description="Drafts, rewrites and summarizes documents using the workspace's files and memories.",
        instructions=(
            "Draft and summarize documents. Ground answers in the workspace's files and memories "
            "and say which ones you used."
        ),
        skills=["anum.document-drafting"],
        knowledge_mode=KnowledgeMode.WORKSPACE,
    ),
    _TemplateSpec(
        id="call",
        name="Call Agent",
        icon="phone",
        color="#16a34a",
        category=AgentCategory.WORK,
        description="Summarizes calls from notes or transcripts. Placing calls needs telephony, which is not set up.",
        instructions=(
            "Summarize calls from the notes or transcript the user gives you: who spoke, what was "
            "decided, follow-ups with owners and dates."
        ),
        model_route=AgentModelRoute.FAST,
        tool_patterns=["telephony.*", "call.*"],
        skills=["anum.document-drafting"],
        wants=[("telephony.*", "Telephony (placing and receiving calls)")],
    ),
)


# --------------------------------------------------------------------------- runtime catalog

_catalog_tools: ToolRegistry | None = None
_catalog_skills: SkillRegistry | None = None


def configure_runtime_catalog(tools: ToolRegistry, skills: SkillRegistry | None = None) -> None:
    """The tools and built-in skills this deployment's runtime has (set by ``main``)."""
    global _catalog_tools, _catalog_skills
    _catalog_tools = tools
    _catalog_skills = skills


def runtime_tools() -> ToolRegistry:
    global _catalog_tools
    if _catalog_tools is None:
        _catalog_tools = default_tool_registry()
    return _catalog_tools


def runtime_skills() -> SkillRegistry:
    global _catalog_skills
    if _catalog_skills is None:
        _catalog_skills = default_skill_registry()
    return _catalog_skills


def _tool_definitions() -> list[ToolDefinition]:
    registry = runtime_tools()
    return [d for d in (registry.definition(name) for name in sorted(registry.names)) if d is not None]


def tool_refusal(definition: ToolDefinition, context: TenantContext) -> str | None:
    """Why the caller may not give an agent this tool, or None when they may."""
    if definition.risk_level == RiskLevel.BLOCKED:
        return "This tool is prohibited by runtime policy."
    roles = {role.lower() for role in context.roles}
    if definition.required_roles and not roles & definition.required_roles:
        needed = " or ".join(sorted(definition.required_roles))
        return f"Needs the {needed} role, which you do not have."
    return None


def _installed_skills(context: TenantContext) -> list[Any]:
    from .skills_api import open_skill_store

    with open_skill_store(context) as store:
        installations = [i for i in store.list_installations(context) if i.enabled]
        versions = {(v.skill_id, v.version): v for v in store.list_versions(context)}
    return [(i, versions.get((i.skill_id, i.version))) for i in installations]


def catalog_skills(context: TenantContext) -> list[AgentCatalogSkill]:
    tools = runtime_tools().names
    skills: list[AgentCatalogSkill] = []
    registry = runtime_skills()
    for manifest in registry.manifests():
        # Planning is not a capability to grant; a skill whose tools are absent is not offered.
        if manifest.id == ALWAYS_SELECTED_SKILL or not manifest.required_tools <= tools:
            continue
        skills.append(
            AgentCatalogSkill(
                id=manifest.id,
                name=manifest.name,
                description=manifest.description,
                source="builtin",
                required_tools=sorted(manifest.required_tools),
            )
        )
    for installation, version in _installed_skills(context):
        skills.append(
            AgentCatalogSkill(
                id=installation.skill_id,
                name=version.name if version else installation.skill_id,
                description=version.description if version else "",
                source="installed",
                required_tools=sorted(set(installation.approved_tools)),
            )
        )
    return skills


def _workspace_floor(context: TenantContext) -> AgentAutonomy:
    """The least restrictive level per category that policy lets run without a person."""
    write = AutonomyLevel.AUTO
    try:
        from .dependencies import memory_repository
        from .settings import settings

        if settings.repository_backend == "memory":
            policy = memory_repository.get_approval_policy(context)
        else:
            from .dependencies import _unit_of_work

            with _unit_of_work(context, require_membership=False) as repository:
                policy = repository.get_approval_policy(context)
        if policy.medium_risk_requires_approval:
            write = AutonomyLevel.ASK
    except Exception:  # unknown policy: show the stricter floor (the runtime enforces it anyway)
        write = AutonomyLevel.ASK
    return AgentAutonomy(read=AutonomyLevel.AUTO, write=write, external=AutonomyLevel.ASK)


def resolve_template(spec: _TemplateSpec, tool_names: set[str], skill_ids: set[str]) -> AgentTemplate:
    tools = sorted(
        name for name in tool_names
        if name != ALWAYS_ALLOWED_TOOL and any(fnmatch.fnmatchcase(name, p) for p in spec.tool_patterns)
    )
    missing = [label for pattern, label in spec.wants if not any(fnmatch.fnmatchcase(n, pattern) for n in tool_names)]
    return AgentTemplate(
        id=spec.id,
        name=spec.name,
        icon=spec.icon,
        color=spec.color,
        category=spec.category,
        description=spec.description,
        instructions=spec.instructions,
        model_route=spec.model_route,
        allowed_tools=tools,
        allowed_skills=[skill for skill in spec.skills if skill in skill_ids],
        autonomy=AgentAutonomy(),
        knowledge_mode=spec.knowledge_mode,
        missing_capabilities=missing,
    )


def build_catalog(context: TenantContext) -> AgentCatalog:
    definitions = _tool_definitions()
    tools = [
        AgentCatalogTool(
            name=d.name,
            description=d.description,
            risk_level=d.risk_level,
            category=ToolCategory.READ if d.name == ALWAYS_ALLOWED_TOOL else agent_tool_category(d.name, d),
            always_on=d.name == ALWAYS_ALLOWED_TOOL,
            available_to_you=tool_refusal(d, context) is None,
            reason=tool_refusal(d, context),
        )
        for d in definitions
        if d.risk_level != RiskLevel.BLOCKED
    ]
    skills = catalog_skills(context)
    usable_tools = {t.name for t in tools if t.available_to_you}
    templates = [resolve_template(spec, usable_tools, {s.id for s in skills}) for spec in TEMPLATES]
    return AgentCatalog(
        tools=tools,
        skills=skills,
        templates=templates,
        icons=list(AGENT_ICONS),
        autonomy_floor=_workspace_floor(context),
    )


# --------------------------------------------------------------------------- validation


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def validate_capabilities(fields: AgentProfileFields, context: TenantContext) -> None:
    """Refuse tools, skills and knowledge the caller cannot give this agent (422 with why)."""
    registry = runtime_tools()
    for name in fields.allowed_tools:
        definition = registry.definition(name)
        if definition is None:
            raise _unprocessable(f"The tool {name} does not exist in this deployment")
        refusal = tool_refusal(definition, context)
        if refusal:
            raise _unprocessable(f"You cannot give an agent the tool {name}: {refusal}")
    skills = {skill.id: skill for skill in catalog_skills(context)}
    allowed = set(fields.allowed_tools) | {ALWAYS_ALLOWED_TOOL}
    for skill_id in fields.allowed_skills:
        skill = skills.get(skill_id)
        if skill is None:
            raise _unprocessable(
                f"The skill {skill_id} is not available: install and enable it in Skills first"
            )
        if skill.source == "builtin":
            missing = sorted(set(skill.required_tools) - allowed)
            if missing:
                raise _unprocessable(f"The skill {skill.name} needs the tool {', '.join(missing)}: allow it too")
    if fields.knowledge_mode == KnowledgeMode.SELECTED:
        visible = _visible_sources(context)
        for source in fields.knowledge_sources:
            if (source.type, source.id) not in visible:
                raise _unprocessable(
                    f"The {source.type} {source.id} is not indexed in this workspace or not visible to you"
                )


def _visible_sources(context: TenantContext) -> set[tuple[str, str]]:
    from .retrieval import open_retrieval_store

    with open_retrieval_store(context) as store:
        return {(str(source.source_type), source.source_id) for source in store.list_sources(context)}


# --------------------------------------------------------------------------- store


class AgentStoreProtocol(Protocol):
    def list(self, context: TenantContext) -> list[AgentProfile]: ...
    def get(self, context: TenantContext, agent_id: str) -> AgentProfile | None: ...
    def count(self, context: TenantContext) -> int: ...
    def save(self, profile: AgentProfile) -> AgentProfile: ...
    def delete(self, context: TenantContext, agent_id: str) -> bool: ...
    def list_memories(self, context: TenantContext, agent_id: str) -> list[AgentMemory]: ...
    def add_memory(self, memory: AgentMemory) -> AgentMemory: ...
    def delete_memory(self, context: TenantContext, agent_id: str, memory_id: str) -> bool: ...
    def record_audit(self, record: AuditRecord) -> None: ...


def visible_to(profile: AgentProfile, context: TenantContext) -> bool:
    return (profile.tenant_id, profile.workspace_id) == (context.tenant_id, context.workspace_id) and (
        profile.sharing == AgentSharing.WORKSPACE or profile.owner_user_id == context.user_id
    )


class InMemoryAgentStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): the same visibility rule as RLS."""

    def __init__(self) -> None:
        self.profiles: dict[str, AgentProfile] = {}
        self.memories: dict[str, list[AgentMemory]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.profiles.clear()
            self.memories.clear()

    def list(self, context: TenantContext) -> list[AgentProfile]:
        with self._lock:
            return [p.model_copy(deep=True) for p in self.profiles.values() if visible_to(p, context)]

    def get(self, context: TenantContext, agent_id: str) -> AgentProfile | None:
        with self._lock:
            profile = self.profiles.get(agent_id)
            return profile.model_copy(deep=True) if profile is not None and visible_to(profile, context) else None

    def count(self, context: TenantContext) -> int:
        with self._lock:
            return sum(
                1 for p in self.profiles.values()
                if (p.tenant_id, p.workspace_id) == (context.tenant_id, context.workspace_id)
            )

    def save(self, profile: AgentProfile) -> AgentProfile:
        with self._lock:
            self.profiles[profile.id] = profile.model_copy(deep=True)
        return profile

    def delete(self, context: TenantContext, agent_id: str) -> bool:
        with self._lock:
            profile = self.profiles.get(agent_id)
            if profile is None or not visible_to(profile, context):
                return False
            del self.profiles[agent_id]
            self.memories.pop(agent_id, None)
            return True

    def list_memories(self, context: TenantContext, agent_id: str) -> list[AgentMemory]:
        with self._lock:
            if self.get(context, agent_id) is None:
                return []
            return list(self.memories.get(agent_id, []))

    def add_memory(self, memory: AgentMemory) -> AgentMemory:
        with self._lock:
            self.memories.setdefault(memory.agent_id, []).append(memory)
        return memory

    def delete_memory(self, context: TenantContext, agent_id: str, memory_id: str) -> bool:
        with self._lock:
            if self.get(context, agent_id) is None:
                return False
            items = self.memories.get(agent_id, [])
            kept = [m for m in items if m.id != memory_id]
            self.memories[agent_id] = kept
            return len(kept) != len(items)

    def record_audit(self, record: AuditRecord) -> None:
        from .dependencies import memory_repository

        memory_repository.record_audit(record)


agent_store = InMemoryAgentStore()


@contextmanager
def open_agent_store(context: TenantContext) -> Iterator[AgentStoreProtocol]:
    """One unit of work in the caller's tenant, workspace and user scope (private agents)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.agent_profiles_repository import SqlAlchemyAgentStore

        return SqlAlchemyAgentStore(session, context)

    with open_scoped_store(context, agent_store, sql_store, user_scoped=True) as store:
        yield store


def load_agent(context: TenantContext, agent_id: str) -> AgentProfile | None:
    with open_agent_store(context) as store:
        return store.get(context, agent_id)


def agent_memories_for_run(context: TenantContext, agent_id: str, limit: int = 20) -> list[AgentMemory]:
    """The agent's newest memories, for a run or a chat (the caller must see the agent)."""
    with open_agent_store(context) as store:
        items = store.list_memories(context, agent_id)
    items.sort(key=lambda m: (m.created_at, m.id), reverse=True)
    return items[:limit]


# --------------------------------------------------------------------------- permissions


def _is_workspace_owner(context: TenantContext) -> bool:
    return "owner" in {role.strip().lower() for role in context.roles}


def can_edit(profile: AgentProfile, context: TenantContext) -> bool:
    if not has_permission(context, Permission.TASK_CREATE):
        return False
    if profile.owner_user_id == context.user_id:
        return True
    return profile.sharing == AgentSharing.WORKSPACE and _is_workspace_owner(context)


EDIT_REFUSAL = "Only the agent's creator, or a workspace owner for a shared agent, can change it"


def _require_edit(profile: AgentProfile, context: TenantContext) -> None:
    if not can_edit(profile, context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EDIT_REFUSAL)


def _view(profile: AgentProfile, context: TenantContext, tool_names: set[str], skill_ids: set[str]) -> AgentProfileView:
    return AgentProfileView(
        **profile.model_dump(),
        can_edit=can_edit(profile, context),
        can_run=has_permission(context, Permission.TASK_RUN) and profile.status == AgentStatus.ACTIVE,
        unavailable_tools=[t for t in profile.allowed_tools if t not in tool_names],
        unavailable_skills=[s for s in profile.allowed_skills if s not in skill_ids],
    )


def _views(profiles: Iterable[AgentProfile], context: TenantContext) -> list[AgentProfileView]:
    tool_names = runtime_tools().names
    try:
        skill_ids = {skill.id for skill in catalog_skills(context)}
    except Exception:  # the skill store is unreadable: report nothing as unavailable rather than everything
        skill_ids = {s for p in profiles for s in p.allowed_skills}
    return [_view(profile, context, tool_names, skill_ids) for profile in profiles]


def _audit(store: AgentStoreProtocol, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome="success",
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata,
        )
    )


def _summary(profile: AgentProfile) -> dict[str, Any]:
    """What an audit record keeps about a profile: configuration, never instruction text."""
    return {
        "name": profile.name,
        "category": profile.category.value,
        "sharing": profile.sharing.value,
        "status": profile.status.value,
        "model_route": profile.model_route.value,
        "allowed_tools": list(profile.allowed_tools),
        "allowed_skills": list(profile.allowed_skills),
        "autonomy": profile.autonomy.model_dump(mode="json"),
        "knowledge_mode": profile.knowledge_mode.value,
        "knowledge_sources": len(profile.knowledge_sources),
    }


def _matches(profile: AgentProfile, query: str) -> bool:
    words = query.lower().split()
    haystack = " ".join([profile.name, profile.description, profile.department or ""]).lower()
    return all(word in haystack for word in words)


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

NOT_FOUND = "Agent not found. It may have been deleted, or it is private to someone else."


def _agent_or_404(store: AgentStoreProtocol, context: TenantContext, agent_id: str) -> AgentProfile:
    profile = store.get(context, agent_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return profile


@router.get("/catalog", response_model=AgentCatalog)
async def agent_catalog(context: TenantContext = Depends(tenant_context)) -> AgentCatalog:
    """Tools, skills, templates and icons an agent can be built from, for the caller."""
    require_permission(context, Permission.TASK_READ)
    return build_catalog(context)


@router.get("", response_model=AgentPage)
async def list_agents(
    category: AgentCategory | None = None,
    agent_status: AgentStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    cursor: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> AgentPage:
    """Agents the caller can see: their own and the workspace's shared ones (newest change first)."""
    require_permission(context, Permission.TASK_READ)
    with open_agent_store(context) as store:
        profiles = store.list(context)
    profiles.sort(key=lambda p: (p.updated_at, p.id), reverse=True)
    if q and q.strip():
        profiles = [p for p in profiles if _matches(p, q)]
    if agent_status is not None:
        profiles = [p for p in profiles if p.status == agent_status]
    counts = {"all": len(profiles)} | {c.value: sum(1 for p in profiles if p.category == c) for c in AgentCategory}
    if category is not None:
        profiles = [p for p in profiles if p.category == category]
    if cursor:
        index = next((i for i, p in enumerate(profiles) if p.id == cursor), None)
        if index is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or invalid cursor")
        profiles = profiles[index + 1 :]
    page = profiles[:limit]
    return AgentPage(
        items=_views(page, context),
        next_cursor=page[-1].id if len(profiles) > limit else None,
        counts=counts,
    )


@router.post("", response_model=AgentProfileView, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentProfileCreate, context: TenantContext = Depends(tenant_context)) -> AgentProfileView:
    return await create_agent_with_status(payload, context, AgentStatus.ACTIVE)


async def create_agent_with_status(payload: AgentProfileCreate, context: TenantContext, initial_status: AgentStatus) -> AgentProfileView:
    """The governed create (permission, capability checks, limit, audit) with the first status set in
    the same write, so a caller that wants a paused agent never leaves an active one behind."""
    require_permission(context, Permission.TASK_CREATE)
    validate_capabilities(payload, context)
    now = utc_now()
    profile = AgentProfile(
        **payload.model_dump(),
        id=new_id("agent"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id,
        status=initial_status,
        created_at=now,
        updated_at=now,
        version=1,
    )
    with open_agent_store(context) as store:
        if store.count(context) >= MAX_AGENTS_PER_WORKSPACE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This workspace has the maximum of {MAX_AGENTS_PER_WORKSPACE} agents. Delete one first.",
            )
        store.save(profile)
        _audit(store, context, "agent.created", profile.id, _summary(profile))
    return _views([profile], context)[0]


@router.get("/{agent_id}", response_model=AgentProfileView)
async def get_agent(agent_id: str, context: TenantContext = Depends(tenant_context)) -> AgentProfileView:
    require_permission(context, Permission.TASK_READ)
    with open_agent_store(context) as store:
        profile = _agent_or_404(store, context, agent_id)
    return _views([profile], context)[0]


@router.patch("/{agent_id}", response_model=AgentProfileView)
async def update_agent(
    agent_id: str, payload: AgentProfileUpdate, context: TenantContext = Depends(tenant_context)
) -> AgentProfileView:
    require_permission(context, Permission.TASK_CREATE)
    changes = payload.model_dump(exclude_unset=True)
    expected_version = changes.pop("version", None)
    with open_agent_store(context) as store:
        profile = _agent_or_404(store, context, agent_id)
        _require_edit(profile, context)
        if expected_version is not None and expected_version != profile.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Someone changed this agent since you opened it. Reload and apply your change again.",
            )
        if "sharing" in changes and changes["sharing"] != profile.sharing and profile.owner_user_id != context.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the agent's creator can change who it is shared with"
            )
        new_status = changes.pop("status", None)
        merged = profile.model_dump(include=set(AgentProfileFields.model_fields)) | changes
        try:
            fields = AgentProfileFields.model_validate(merged)
        except ValueError as exc:
            raise _unprocessable(str(exc)) from exc
        capability_keys = {"allowed_tools", "allowed_skills", "knowledge_mode", "knowledge_sources"}
        if capability_keys & set(changes):
            validate_capabilities(fields, context)
        updated = AgentProfile(
            **fields.model_dump(),
            id=profile.id,
            tenant_id=profile.tenant_id,
            workspace_id=profile.workspace_id,
            owner_user_id=profile.owner_user_id,
            status=AgentStatus(new_status) if new_status else profile.status,
            created_at=profile.created_at,
            updated_at=utc_now(),
            version=profile.version + 1,
        )
        store.save(updated)
        changed = sorted(set(changes) | ({"status"} if new_status and new_status != profile.status else set()))
        _audit(store, context, "agent.updated", agent_id, {"changed": changed, "after": _summary(updated)})
    return _views([updated], context)[0]


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_agent(agent_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Delete the agent and its memories. Its tasks and chats stay in the workspace history."""
    require_permission(context, Permission.TASK_CREATE)
    with open_agent_store(context) as store:
        profile = _agent_or_404(store, context, agent_id)
        _require_edit(profile, context)
        store.delete(context, agent_id)
        _audit(store, context, "agent.deleted", agent_id, {"name": profile.name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/memories", response_model=AgentMemoryPage)
async def list_agent_memories(
    agent_id: str,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    cursor: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> AgentMemoryPage:
    require_permission(context, Permission.MEMORY_READ)
    with open_agent_store(context) as store:
        _agent_or_404(store, context, agent_id)
        items = store.list_memories(context, agent_id)
    items.sort(key=lambda m: (m.created_at, m.id), reverse=True)
    if q and q.strip():
        words = q.lower().split()
        items = [m for m in items if all(w in m.content.lower() for w in words)]
    total = len(items)
    if cursor:
        index = next((i for i, m in enumerate(items) if m.id == cursor), None)
        if index is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or invalid cursor")
        items = items[index + 1 :]
    page = items[:limit]
    return AgentMemoryPage(items=page, next_cursor=page[-1].id if len(items) > limit else None, total=total)


@router.post("/{agent_id}/memories", response_model=AgentMemory, status_code=status.HTTP_201_CREATED)
async def create_agent_memory(
    agent_id: str, payload: AgentMemoryCreate, context: TenantContext = Depends(tenant_context)
) -> AgentMemory:
    require_permission(context, Permission.MEMORY_CREATE)
    if payload.task_id is not None:
        _require_agent_task(context, agent_id, payload.task_id)
    with open_agent_store(context) as store:
        profile = _agent_or_404(store, context, agent_id)
        _require_edit(profile, context)
        if len(store.list_memories(context, agent_id)) >= MAX_AGENT_MEMORIES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This agent has the maximum of {MAX_AGENT_MEMORIES} memories. Delete some first.",
            )
        memory = AgentMemory(
            id=new_id("agent_memory"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            agent_id=agent_id,
            content=payload.content,
            source=AgentMemorySource.TASK if payload.task_id else AgentMemorySource.MANUAL,
            task_id=payload.task_id,
            created_by=context.user_id,
            created_at=utc_now(),
        )
        store.add_memory(memory)
        _audit(store, context, "agent_memory.created", memory.id, {"agent_id": agent_id, "source": memory.source.value})
    return memory


def _require_agent_task(context: TenantContext, agent_id: str, task_id: str) -> None:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        task = repository.get_task(task_id, context)
    if task is None or task.agent_id != agent_id:
        raise _unprocessable("That task was not run by this agent")


@router.delete("/{agent_id}/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_agent_memory(agent_id: str, memory_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MEMORY_DELETE)
    with open_agent_store(context) as store:
        profile = _agent_or_404(store, context, agent_id)
        _require_edit(profile, context)
        if not store.delete_memory(context, agent_id, memory_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
        _audit(store, context, "agent_memory.deleted", memory_id, {"agent_id": agent_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# The skill loadout routes (GET /{agent_id}/skills, GET /skills/usage) live in their own module
# and register on this router; imported last so every name it needs exists.
from . import agent_skill_loadout  # noqa: E402,F401
