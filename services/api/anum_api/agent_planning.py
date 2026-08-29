from pydantic import BaseModel, Field

from .agent_skills import SkillManifest, SkillRegistry
from .agent_tools import ToolCall, ToolRegistry
from .model_gateway import ModelGateway, ModelResponse
from .schemas import Task


class AgentPlan(BaseModel):
    objective: str
    skill_ids: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)


class PlanResult(BaseModel):
    plan: AgentPlan
    model_response: ModelResponse


class AgentPlanner:
    def __init__(
        self,
        model_gateway: ModelGateway,
        skills: SkillRegistry,
        tools: ToolRegistry,
    ) -> None:
        self.model_gateway = model_gateway
        self.skills = skills
        self.tools = tools

    async def plan(self, task: Task) -> PlanResult:
        selected = self.skills.select(task.prompt, self.tools.names)
        response = await self.model_gateway.generate_text(task.prompt)
        call = self._select_tool(task.prompt, selected, response)
        return PlanResult(
            plan=AgentPlan(
                objective=task.prompt,
                skill_ids=[skill.id for skill in selected],
                tool_calls=[call],
            ),
            model_response=response,
        )

    def _select_tool(
        self,
        prompt: str,
        skills: list[SkillManifest],
        response: ModelResponse,
    ) -> ToolCall:
        if any(skill.id == "anum.external-action" for skill in skills):
            return ToolCall(
                name="external.action",
                arguments={"action": prompt, "planned_response": response.text},
            )
        return ToolCall(name="anum.respond", arguments={"text": response.text})
