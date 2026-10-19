from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .agent_skills import SkillManifest, SkillRegistry
from .agent_tools import ToolCall, ToolRegistry
from .model_gateway import ModelGateway, ModelResponse, ModelRoute, route_gateway
from .retrieval import build_cache_layout_prompt, build_task_prompt
from .schemas import Task
from .team_commands import looks_like_assignment

if TYPE_CHECKING:
    from .agent_policy import AgentRunProfile


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

    async def plan(
        self,
        task: Task,
        *,
        context_blocks: Sequence[str] = (),
        learned_block: str | None = None,
        agent: "AgentRunProfile | None" = None,
    ) -> PlanResult:
        """Plan ``task``; ``context_blocks`` are provenance-labeled retrieved data.

        Skills and the tool are chosen from the user's own prompt only. Retrieved
        blocks reach the model as labeled data and can never select a skill or tool
        (threat model G5); tool policy is evaluated by the runtime afterwards.
        ``learned_block`` (the user's learned style and preferences, docs/learning.md)
        precedes the task in the model prompt only; it never selects a skill or tool.

        As an ``agent`` (docs/agents.md): only the agent's skills and tools can be
        selected, its persona and instructions come before the task in the model
        prompt (they never select anything), and its model route is used.
        """
        tools = self.tools.names
        if agent is not None:
            tools = {name for name in tools if agent.tool_allowed(name)}
        selected = self.skills.select(task.prompt, tools)
        if agent is not None:
            selected = [skill for skill in selected if agent.skill_allowed(skill.id)]
        # Planning is agent work: the capable model (docs/model-gateway.md#model-routing),
        # unless the agent asks for the fast one.
        route = ModelRoute(agent.model_route) if agent is not None else ModelRoute.CAPABLE
        planner = route_gateway(self.model_gateway, route)
        # As an agent, the call counts against that agent's monthly cost cap and usage
        # (docs/agent-controls.md); gateways without budgets have no ``for_agent``.
        bind_agent = getattr(planner, "for_agent", None) if agent is not None else None
        if callable(bind_agent) and agent is not None:
            planner = bind_agent(agent.id)
        # The agent's persona, then the user's learned style, then the task: text for
        # the model only, never what selects a skill or tool.
        preface = [part for part in (agent.persona() if agent is not None else None, learned_block) if part]
        prompt = "\n\n".join([*preface, f"Task:\n{task.prompt}"]) if preface else task.prompt
        response = await self._generate(planner, prompt, context_blocks)
        # As an agent, only its tools can be chosen (team work's ``team.assign_task`` too).
        call = _brevo_call(task.prompt, selected) or _marketing_call(task, selected) or (
            self._select_tool(task.prompt, selected, response)
            if agent is None
            else self._select_tool(task.prompt, selected, response, tools)
        )
        # A Brevo call with no working connection becomes a reply that says what to fix.
        from .brevo.planning import refusal as brevo_refusal

        call = await brevo_refusal(task, call) or call
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
        available_tools: Collection[str] | None = None,
    ) -> ToolCall:
        tools = self.tools.names if available_tools is None else available_tools
        return choose_tool_call(prompt, skills, response.text, tools)


def _fleet_work_words(prompt: str) -> bool:
    import re

    return bool(re.search(r"\b(work\s*orders?|job\s*cards?|workshop|maintenance)\b|أوامر العمل|أمر عمل|ورشة|صيانة",
                          prompt, re.IGNORECASE))


def choose_tool_call(
    prompt: str, skills: Sequence[SkillManifest], planned_text: str, available_tools: Collection[str] = ()
) -> ToolCall:
    """The one tool call a task's prompt and selected skills lead to.

    Deterministic and free of model output except ``planned_text`` (the model's answer,
    used only by ``anum.respond``), so the command plan preview (docs/command.md) can show
    exactly which tool a step will use before anything runs.
    """
    if "team.assign_task" in available_tools and looks_like_assignment(prompt):
        # "ask Ahmed to call the supplier by Thursday": the tool parses the user's own
        # sentence (never model output) and resolves the person in the workspace.
        return ToolCall(name="team.assign_task", arguments={"command": " ".join(prompt.split())[:500]})
    brevo_call = _brevo_call(prompt, skills)
    if brevo_call is not None:
        return brevo_call
    if any(skill.id == "anum.whatsapp-inbox" for skill in skills):
        # Read-only: which conversations need attention. Replies are drafted and sent
        # from the inbox, where a person approves them (docs/whatsapp.md#agent-tools).
        words = set(prompt.lower().split())
        waiting = bool(
            words & {"reply", "replies", "unanswered", "answer", "waiting", "ينتظر", "تنتظر", "بانتظار", "رد", "الرد"}
        )
        return ToolCall(
            name="list_whatsapp_conversations",
            arguments={"filter": "needs_reply" if waiting else "all", "limit": 20},
        )
    if any(skill.id == "anum.email" for skill in skills):
        # Mail questions read the inbox; filters come from the user's own words (threat
        # model G5). Sending is never planned here: it is a draft approved in the Inbox.
        from .mailbox.config import mail_settings

        if mail_settings.enabled:
            # The Email Center (docs/email.md#assistant): summarise, label, draft, or ask for
            # an approval to forward, archive, export or delete, from the user's own words.
            from .mailbox.planning import plan_mail_call

            mail_call = plan_mail_call(prompt, available_tools)
            if mail_call is not None:
                return mail_call
        from .email_inbox import SEARCH_TOOL, tool_arguments_from_prompt

        return ToolCall(name=SEARCH_TOOL, arguments=tool_arguments_from_prompt(prompt))
    if any(skill.id == "anum.tyre-planning" for skill in skills) and _fleet_work_words(prompt):
        # "open work orders for truck 12" is fleet maintenance, not a sales order.
        from .tyre.tools import choose_call as choose_tyre_call

        return choose_tyre_call(prompt)
    if any(skill.id == "anum.commerce" for skill in skills):
        # Parsed from the user's own prompt only (threat model G5); None falls through.
        from .commerce.tools import plan_call

        call = plan_call(prompt)
        if call is not None:
            if call.name == "anum.respond" and "text" not in call.arguments:
                call.arguments["text"] = planned_text
            return call
    if any(skill.id == "anum.finance" for skill in skills):
        # Questions about the caller's own money, from the user's own words (threat model
        # G5). Paying or transferring returns None and falls through to external-action.
        from .finance.tools import plan_call as plan_finance_call

        finance_call = plan_finance_call(prompt)
        if finance_call is not None:
            return finance_call
    if any(skill.id == "anum.external-action" for skill in skills):
        return ToolCall(
            name="external.action",
            arguments={"action": prompt, "planned_response": planned_text},
        )
    if any(skill.id == "anum.tyre-planning" for skill in skills):
        # Read-only tyre tools; the tool and its arguments come from the user's own words.
        from .tyre.tools import choose_call

        return choose_call(prompt)
    if any(skill.id == "anum.web-search" for skill in skills):
        # The query is the user's own prompt, never retrieved text or model output
        # (threat model G5); the tool caps its length.
        return ToolCall(name="web_search", arguments={"query": " ".join(prompt.split())[:400]})
    return ToolCall(name="anum.respond", arguments={"text": planned_text})


def _marketing_call(task: Task, skills: Sequence[SkillManifest]) -> ToolCall | None:
    """Drafts, publish requests and performance questions about the workspace's own social
    accounts (docs/marketing.md#assistant), chosen from the user's own prompt. Publishing
    pauses for approval."""
    if not any(skill.id == "anum.marketing" for skill in skills):
        return None
    from .marketing.tools import planned_call

    return planned_call(task, task.prompt)


def _brevo_call(prompt: str, skills: Sequence[SkillManifest]) -> ToolCall | None:
    """Brevo email campaign drafts and reads (docs/brevo.md#planner), from the user's own
    prompt. Never a send: that waits for an owner's approval of the exact draft."""
    if not any(skill.id == "anum.brevo" for skill in skills):
        return None
    from .brevo.planning import plan_call

    return plan_call(prompt)
