from collections.abc import Sequence

from pydantic import BaseModel, Field

from .agent_skills import SkillManifest, SkillRegistry
from .agent_tools import ToolCall, ToolRegistry
from .model_gateway import ModelGateway, ModelResponse, ModelRoute, route_gateway
from .retrieval import build_cache_layout_prompt, build_task_prompt
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

    async def plan(self, task: Task, *, context_blocks: Sequence[str] = ()) -> PlanResult:
        """Plan ``task``; ``context_blocks`` are provenance-labeled retrieved data.

        Skills and the tool are chosen from the user's own prompt only. Retrieved
        blocks reach the model as labeled data and can never select a skill or tool
        (threat model G5); tool policy is evaluated by the runtime afterwards.
        """
        selected = self.skills.select(task.prompt, self.tools.names)
        # Planning is agent work: the capable model (docs/model-gateway.md#model-routing).
        planner = route_gateway(self.model_gateway, ModelRoute.CAPABLE)
        response = await self._generate(planner, task.prompt, context_blocks)
        call = self._select_tool(task.prompt, selected, response)
        return PlanResult(
            plan=AgentPlan(
                objective=task.prompt,
                skill_ids=[skill.id for skill in selected],
                tool_calls=[call],
            ),
            model_response=response,
        )

    @staticmethod
    async def _generate(planner: ModelGateway, prompt: str, context_blocks: Sequence[str]) -> ModelResponse:
        """One planning call. A gateway that caches instructions (``anthropic``) gets the
        stable instructions first, as a cached prefix, and the task last; every other
        provider gets the single prompt it always has (docs/model-gateway.md)."""
        layout = build_cache_layout_prompt(prompt, context_blocks)
        cached = getattr(planner, "generate_with_instructions", None)
        if layout is not None and getattr(planner, "caches_instructions", False) is True and callable(cached):
            response: ModelResponse = await cached(layout.instructions, layout.content)
            return response
        return await planner.generate_text(build_task_prompt(prompt, context_blocks))

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
        if any(skill.id == "anum.web-search" for skill in skills):
            # The query is the user's own prompt, never retrieved text or model output
            # (threat model G5); the tool caps its length.
            return ToolCall(name="web_search", arguments={"query": " ".join(prompt.split())[:400]})
        return ToolCall(name="anum.respond", arguments={"text": response.text})
