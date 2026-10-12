"""What an agent loads: its skills, resolved against this deployment and workspace.

docs/agents.md#skills. An agent's ``allowed_skills`` name built-in runtime skills and skills
installed in the workspace ([Skills](skills.md)). This module answers, for one agent and the
caller:

- ``GET /api/v1/agents/{id}/skills``: each skill with its source, version, risk, the tools it
  needs (registered here, allowed on the agent, granted by the installation), how it loads
  (built-in skills when a task's words call for them, installed skills into every run and
  chat) and its state: ``ready``, ``needs_tools``, ``disabled``, ``not_installed``,
  ``blocked`` or ``unavailable``, with a sentence saying who can fix it.
- ``GET /api/v1/agents/skills/usage``: which of the agents the caller can see load each skill
  (the Skills page shows it and warns before a skill is disabled or uninstalled).
- :func:`agent_run_profile`: the run snapshot of an agent, with the installed skills that are
  ready resolved to their instructions (capped), so a task or a chat as the agent really
  loads them. Skill instructions shape answers only; tools and approvals stay with the
  runtime's policy and the agent's own allowlist (a skill never widens either).

Everything reads through the caller's tenant context: the agent store under RLS (another
user's private agent is neither listed nor counted) and the workspace's skill store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field

from .agent_profiles import (
    ALWAYS_ALLOWED_TOOL,
    ALWAYS_SELECTED_SKILL,
    NOT_FOUND,
    AgentProfile,
    AgentStatus,
    can_edit,
    open_agent_store,
    router,
    runtime_skills,
    runtime_tools,
)
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import RiskLevel, TenantContext

if TYPE_CHECKING:  # agent_policy imports agent_profiles, which imports this module last
    from .agent_policy import AgentRunProfile, LoadedSkill

SkillState = Literal["ready", "needs_tools", "disabled", "not_installed", "blocked", "unavailable"]
MAX_TRIGGERS_SHOWN = 12


class AgentSkillTool(BaseModel):
    name: str
    # The tool exists in this deployment.
    registered: bool
    # The agent may use it (its allowed tools, or the always-on text tool).
    allowed: bool
    # Granted by the workspace installation (installed skills only; null for built-in ones).
    granted: bool | None = None


class AgentSkillState(BaseModel):
    id: str
    name: str
    description: str = ""
    source: Literal["builtin", "installed", "unknown"]
    version: str | None = None
    latest_version: str | None = None
    risk_level: RiskLevel | None = None
    state: SkillState
    # Why it is not ready, and who can change that; null when ready.
    reason: str | None = None
    # ``on_request``: a built-in skill is selected when a task's words call for it.
    # ``always``: an installed skill's instructions go into every run and chat as the agent.
    activation: Literal["on_request", "always"]
    triggers: list[str] = Field(default_factory=list)
    loads_instructions: bool = False
    upgrade_available: bool = False
    tools: list[AgentSkillTool] = Field(default_factory=list)


class AgentSkillLoadout(BaseModel):
    agent_id: str
    agent_version: int
    can_edit: bool
    items: list[AgentSkillState]
    ready: int
    attention: int


class SkillUsageAgent(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    status: AgentStatus


class SkillUsage(BaseModel):
    skill_id: str
    count: int
    agents: list[SkillUsageAgent]


class SkillUsagePage(BaseModel):
    items: list[SkillUsage]


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def _join(names: list[str]) -> str:
    return ", ".join(names)


def skill_states(profile: AgentProfile, context: TenantContext) -> tuple[list[AgentSkillState], dict[str, str]]:
    """Each of the agent's skills as this deployment and workspace treat it, and the
    instructions of the installed ones that are ready (by skill id)."""
    from .agent_policy import LOADED_SKILL_CHARS
    from .skills_api import open_skill_store

    tools_here = runtime_tools().names
    agent_tools = set(profile.allowed_tools) | {ALWAYS_ALLOWED_TOOL}
    registry = runtime_skills()
    with open_skill_store(context) as store:
        installations = {i.skill_id: i for i in store.list_installations(context)}
        versions = store.list_versions(context)
    by_skill: dict[str, list] = {}
    for version in versions:
        by_skill.setdefault(version.skill_id, []).append(version)
    for listed in by_skill.values():
        listed.sort(key=lambda v: _version_key(v.version), reverse=True)

    items: list[AgentSkillState] = []
    instructions: dict[str, str] = {}
    for skill_id in profile.allowed_skills:
        manifest = registry.get(skill_id)
        if manifest is not None and skill_id != ALWAYS_SELECTED_SKILL:
            tools = [
                AgentSkillTool(name=name, registered=name in tools_here, allowed=name in agent_tools)
                for name in sorted(manifest.required_tools)
            ]
            missing_here = [t.name for t in tools if not t.registered]
            not_allowed = [t.name for t in tools if t.registered and not t.allowed]
            if missing_here:
                state: SkillState = "unavailable"
                reason = f"This deployment does not have the tool {_join(missing_here)}; an operator adds it with its connector."
            elif not_allowed:
                state, reason = "needs_tools", f"Allow the tool {_join(not_allowed)} on this agent so the skill can run."
            else:
                state, reason = "ready", None
            items.append(
                AgentSkillState(
                    id=skill_id, name=manifest.name, description=manifest.description, source="builtin",
                    version=manifest.version, latest_version=manifest.version, risk_level=manifest.risk_level,
                    state=state, reason=reason, activation="on_request" if manifest.triggers else "always",
                    triggers=sorted(manifest.triggers, key=lambda w: (not w.isascii(), w))[:MAX_TRIGGERS_SHOWN],
                    tools=tools,
                )
            )
            continue
        published = by_skill.get(skill_id, [])
        installation = installations.get(skill_id)
        if installation is None:
            items.append(
                AgentSkillState(
                    id=skill_id, name=published[0].name if published else skill_id,
                    description=published[0].description if published else "",
                    source="installed" if published else "unknown",
                    latest_version=published[0].version if published else None,
                    risk_level=published[0].risk_level if published else None,
                    state="not_installed" if published else "unavailable",
                    reason=(
                        "It is not installed in this workspace. A workspace owner installs it in Skills."
                        if published else "No skill with this id exists here any more. Remove it from the agent."
                    ),
                    activation="always",
                )
            )
            continue
        version = next((v for v in published if v.version == installation.version), None)
        latest = published[0] if published else None
        granted = set(installation.approved_tools)
        required = sorted(set(version.required_tools)) if version else []
        tools = [
            AgentSkillTool(name=name, registered=name in tools_here, allowed=name in agent_tools, granted=name in granted)
            for name in required
        ]
        reason = None
        if version is None:
            state, reason = "unavailable", "The installed version is no longer published. A workspace owner reinstalls it in Skills."
        elif not installation.enabled:
            state, reason = "disabled", "A workspace owner turned this skill off in Skills. Agents load it again once it is enabled."
        elif version.risk_level == RiskLevel.BLOCKED:
            state, reason = "blocked", "This version is marked blocked, so the runtime never loads it."
        elif missing := [t.name for t in tools if not t.registered]:
            state, reason = "unavailable", f"This deployment does not have the tool {_join(missing)}."
        elif ungranted := [t.name for t in tools if not t.granted]:
            state, reason = "needs_tools", f"The installation did not grant {_join(ungranted)}. A workspace owner reinstalls it with that tool."
        elif not_allowed := [t.name for t in tools if not t.allowed]:
            state, reason = "needs_tools", f"Allow the tool {_join(not_allowed)} on this agent so the skill can run."
        else:
            state = "ready"
            instructions[skill_id] = version.instructions[:LOADED_SKILL_CHARS]
        items.append(
            AgentSkillState(
                id=skill_id, name=version.name if version else (latest.name if latest else skill_id),
                description=version.description if version else "", source="installed",
                version=installation.version, latest_version=latest.version if latest else None,
                risk_level=version.risk_level if version else None, state=state, reason=reason,
                activation="always", loads_instructions=state == "ready",
                upgrade_available=bool(latest and _version_key(latest.version) > _version_key(installation.version)),
                tools=tools,
            )
        )
    return items, instructions


def loaded_skills(profile: AgentProfile, context: TenantContext) -> list[LoadedSkill]:
    """The installed skills a run or chat as ``profile`` loads now (ready ones only)."""
    from .agent_policy import LoadedSkill

    if not any(runtime_skills().get(s) is None for s in profile.allowed_skills):
        return []  # only built-in skills: nothing to read from the skill store
    items, instructions = skill_states(profile, context)
    agent_tools = set(profile.allowed_tools) | {ALWAYS_ALLOWED_TOOL}
    return [
        LoadedSkill(
            id=item.id, version=item.version or "", name=item.name, instructions=instructions[item.id],
            granted_tools=sorted(t.name for t in item.tools if t.granted and t.name in agent_tools),
        )
        for item in items
        if item.id in instructions
    ]


def agent_run_profile(context: TenantContext, profile: AgentProfile) -> AgentRunProfile:
    """The snapshot a task or chat as the agent runs with, its installed skills loaded.

    A failure to read the skill store propagates: an agent never runs without the skills it
    was configured with while the caller thinks they are loaded.
    """
    from .agent_policy import AgentRunProfile

    snapshot = AgentRunProfile.from_profile(profile)
    snapshot.loaded_skills = loaded_skills(profile, context)
    return snapshot


# --------------------------------------------------------------------------- routes
# Registered on the agents router (``anum_api.agent_profiles`` imports this module at its end,
# before ``main`` includes the router). ``/skills/usage`` has two segments, so it never
# collides with ``/{agent_id}``.


@router.get("/skills/usage", response_model=SkillUsagePage)
async def skill_usage(context: TenantContext = Depends(tenant_context)) -> SkillUsagePage:
    """Which of the agents the caller can see load each skill."""
    require_permission(context, Permission.TASK_READ)
    with open_agent_store(context) as store:
        profiles = store.list(context)
    profiles.sort(key=lambda p: p.name.lower())
    usage: dict[str, list[SkillUsageAgent]] = {}
    for profile in profiles:
        for skill_id in profile.allowed_skills:
            usage.setdefault(skill_id, []).append(
                SkillUsageAgent(id=profile.id, name=profile.name, icon=profile.icon, color=profile.color, status=profile.status)
            )
    return SkillUsagePage(
        items=[SkillUsage(skill_id=skill_id, count=len(agents), agents=agents) for skill_id, agents in sorted(usage.items())]
    )


@router.get("/{agent_id}/skills", response_model=AgentSkillLoadout)
async def agent_skills(agent_id: str, context: TenantContext = Depends(tenant_context)) -> AgentSkillLoadout:
    """The agent's skills with their state, version, tools and how they load."""
    require_permission(context, Permission.TASK_READ)
    with open_agent_store(context) as store:
        profile = store.get(context, agent_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    items, _ = skill_states(profile, context)
    ready = sum(1 for item in items if item.state == "ready")
    return AgentSkillLoadout(
        agent_id=profile.id, agent_version=profile.version, can_edit=can_edit(profile, context),
        items=items, ready=ready, attention=len(items) - ready,
    )
