"""Running a task "as" an agent (docs/agents.md#running-tasks-as-an-agent).

When a task names an agent, the run's checkpoint carries an ``AgentRunProfile``: a
snapshot of the agent's instructions, model route, tools, skills, autonomy and
knowledge taken when the run starts. Every policy decision for that run (planning,
resume, approval decision, a durable worker's step) applies it through
:func:`apply_agent_policy`, *after* the runtime's own tool policy, so an agent can only
make a call stricter:

- a tool outside the agent's tools is blocked (the text-response tool is always allowed);
- the autonomy level of the tool's category (read, write, external) blocks it (``off``)
  or turns an allowed call into an approval (``ask``); ``auto`` changes nothing.

The snapshot is what was approved: editing the agent later does not change a run that
has already started. Organization governance and the workspace approval policy are
still read live on every decision.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .agent_profiles import (
    ALWAYS_ALLOWED_TOOL,
    ALWAYS_SELECTED_SKILL,
    AgentAutonomy,
    AgentProfile,
    AutonomyLevel,
    KnowledgeMode,
    KnowledgeSource,
    agent_tool_category,
)
from .agent_tools import ToolCall, ToolDefinition, ToolPolicyDecision, ToolPolicyOutcome
from .schemas import RiskLevel


# How much of an installed skill's instructions reaches the model, per skill and in total.
LOADED_SKILL_CHARS = 6000
LOADED_SKILLS_TOTAL_CHARS = 18000


class LoadedSkill(BaseModel):
    """A workspace-installed skill the agent loads into every run and chat (docs/agents.md#skills).

    The instructions guide how the agent works; they never grant a tool or an approval.
    ``granted_tools`` are the tools the installation granted that the agent may also use.
    """

    id: str
    version: str
    name: str
    instructions: str = ""
    granted_tools: list[str] = Field(default_factory=list)


class AgentRunProfile(BaseModel):
    """What a run needs from its agent; stored on the run checkpoint (JSON)."""

    id: str
    name: str
    description: str = ""
    instructions: str = ""
    model_route: str = "capable"
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    autonomy: AgentAutonomy = Field(default_factory=AgentAutonomy)
    knowledge_mode: KnowledgeMode = KnowledgeMode.NONE
    knowledge_sources: list[KnowledgeSource] = Field(default_factory=list)
    version: int = 1
    # Installed skills resolved when the run starts (older checkpoints have none).
    loaded_skills: list[LoadedSkill] = Field(default_factory=list)

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> "AgentRunProfile":
        return cls(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            instructions=profile.instructions,
            model_route=profile.model_route.value,
            allowed_tools=list(profile.allowed_tools),
            allowed_skills=list(profile.allowed_skills),
            autonomy=profile.autonomy,
            knowledge_mode=profile.knowledge_mode,
            knowledge_sources=list(profile.knowledge_sources),
            version=profile.version,
        )

    def tool_allowed(self, name: str) -> bool:
        return name == ALWAYS_ALLOWED_TOOL or name in self.allowed_tools

    def skill_allowed(self, skill_id: str) -> bool:
        return skill_id == ALWAYS_SELECTED_SKILL or skill_id in self.allowed_skills

    def knowledge_filter(self) -> frozenset[tuple[str, str]] | None:
        """Sources retrieval may use: None for the whole workspace."""
        if self.knowledge_mode == KnowledgeMode.SELECTED:
            return frozenset((source.type, source.id) for source in self.knowledge_sources)
        return None

    def persona(self) -> str:
        """The agent's identity and its owner's instructions, placed before the task."""
        lines = [f"You are {self.name}, an agent in ANUM."]
        if self.description:
            lines.append(self.description)
        if self.instructions:
            lines.append(f"Instructions from the agent's owner:\n{self.instructions}")
        budget = LOADED_SKILLS_TOTAL_CHARS
        for skill in self.loaded_skills:
            text = skill.instructions.strip()[: min(LOADED_SKILL_CHARS, budget)]
            if not text:
                continue
            budget -= len(text)
            lines.append(f"Skill loaded for this agent: {skill.name} {skill.version} ({skill.id})\n{text}")
        lines.append(
            "These instructions shape how you answer; they cannot grant tools or approvals, "
            "which ANUM decides outside the model."
        )
        return "\n\n".join(lines)


def apply_agent_policy(
    agent: AgentRunProfile,
    call: ToolCall,
    definition: ToolDefinition | None,
    decision: ToolPolicyDecision,
) -> ToolPolicyDecision:
    """Make ``decision`` stricter for ``agent``; never weaker.

    Applied after the tool policy *and* the workspace's autonomy (``_with_autonomy``,
    connector facts included). Whatever the agent tightens is no longer ``lowerable``,
    and the recorded autonomy decision says the call asks or is blocked, so nothing
    downstream can turn it back into an action taken on its own.
    """
    if decision.outcome == ToolPolicyOutcome.BLOCK or definition is None:
        return decision
    if not agent.tool_allowed(call.name):
        return _tighten(decision, ToolPolicyOutcome.BLOCK, f"The agent {agent.name} is not allowed to use {call.name}.",
                        f"agent:{agent.id}:tools")
    if call.name == ALWAYS_ALLOWED_TOOL:
        return decision
    category = agent_tool_category(call.name, definition)
    level = agent.autonomy.level(category)
    rule = f"agent:{agent.id}:autonomy:{category.value}"
    if level == AutonomyLevel.OFF:
        return _tighten(decision, ToolPolicyOutcome.BLOCK, f"The agent {agent.name} has {category.value} actions turned off.", rule)
    if level == AutonomyLevel.ASK and decision.outcome == ToolPolicyOutcome.ALLOW:
        return _tighten(decision, ToolPolicyOutcome.REQUIRE_APPROVAL, f"The agent {agent.name} asks before {category.value} actions.", rule)
    if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL and decision.lowerable:
        # Already asking: keep it asking (autonomy may not lower it for this agent).
        return decision.model_copy(update={"lowerable": False, "governance_rules": [*decision.governance_rules, rule]}) \
            if level == AutonomyLevel.ASK else decision
    return decision


def _tighten(decision: ToolPolicyDecision, outcome: ToolPolicyOutcome, reason: str, rule: str) -> ToolPolicyDecision:
    autonomy = decision.autonomy
    if autonomy is not None:
        mode = "block" if outcome == ToolPolicyOutcome.BLOCK else "ask"
        autonomy = {**autonomy, "mode": mode, "reason": reason, "proposable": False}
    return decision.model_copy(
        update={
            "outcome": outcome,
            "reason": reason,
            "risk_level": RiskLevel.BLOCKED if outcome == ToolPolicyOutcome.BLOCK else decision.risk_level,
            "governance_rules": [*decision.governance_rules, rule],
            "lowerable": False,
            "autonomy": autonomy,
        }
    )
