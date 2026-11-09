import re
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .agent_skills import SkillManifest, SkillRegistry
from .agent_tools import ToolCall, ToolRegistry
from .current_time import with_time_line
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
        time_line: str | None = None,
        capabilities: str | None = None,
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
        ``time_line`` (the current date and time in the user's zone) follows the task.
        ``capabilities`` (what ANUM can do in this workspace, ``capabilities.task_capability_block``)
        precedes the task for a text answer, so the model never claims ANUM cannot do something it
        can; like the persona, it never selects a skill or tool.
        """
        tools = self.tools.names
        if agent is not None:
            tools = {name for name in tools if agent.tool_allowed(name)}
        selected = self.skills.select(task.prompt, tools, include_agent_only=agent is not None)
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
        preface = [part for part in (capabilities, agent.persona() if agent is not None else None, learned_block) if part]
        prompt = "\n\n".join([*preface, f"Task:\n{task.prompt}"]) if preface else task.prompt
        # The current date and time (current_time.py) follows the task, outside the cached
        # instructions, so "by Friday" and "tomorrow" resolve against the user's real today.
        prompt = with_time_line(prompt, time_line)
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
        # A website errand (with its address, or without one: the site search) whose action browser
        # is off here is answered with the sentence naming the switch, never a scope approval for
        # nothing or a model refusal (docs/action-browser.md#switches-and-availability).
        call = await self._act_refusal(task, call) or call
        if call.name == "web.act":
            # The logins saved for the approved sites and the cards the prompt names, as handles
            # only (docs/action-browser.md#how-it-is-used): read from the vault, never decrypted.
            from .action_browser.scope import action_scope

            call = await action_scope(task, call)
        return PlanResult(
            plan=AgentPlan(
                objective=task.prompt,
                skill_ids=[skill.id for skill in selected],
                tool_calls=[call],
            ),
            model_response=response,
        )

    async def _act_refusal(self, task: Task, call: ToolCall) -> ToolCall | None:
        errand = call.arguments.get("purpose") == FIND_SITE or (call.name == "anum.respond" and call.arguments.get("errand") is True)
        if call.name != "web.act" and not errand:
            return None
        handler = self.tools.handler("web.act") if hasattr(self.tools, "handler") else None
        check = getattr(handler, "unavailable_reason", None)
        if not callable(check):
            return None
        from .schemas import TenantContext

        context = TenantContext(tenant_id=task.tenant_id, workspace_id=task.workspace_id, user_id=task.created_by or "", roles=[])
        try:
            reason = await check(context)
        except Exception:  # unreadable: plan as before; the tool reports the real state when it runs
            return None
        if not reason:
            return None
        return ToolCall(name="anum.respond", arguments={"text": act_off_text(str(reason), arabic=_is_arabic(task.prompt))})

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


_HTTPS_ADDRESS = re.compile(r"https://[^\s<>\"'`]+", re.IGNORECASE)


def first_https_address(prompt: str) -> str | None:
    """The first https address written in the prompt, without trailing punctuation."""
    found = _HTTPS_ADDRESS.search(prompt)
    if found is None:
        return None
    address = found.group(0).rstrip(".,;:!?)]}،")
    return address[:2048] if len(address) > len("https://") else None


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
    if any(skill.id == "anum.action-browser" for skill in skills):
        # The goal, the start address and the sites come from the user's own prompt, never from
        # retrieved text, a page or model output (threat model G5); the owner approves them
        # before anything opens (docs/action-browser.md#how-it-is-used).
        act_call = _action_browser_call(prompt)
        if act_call is not None:
            return act_call
    gc_call = _green_concrete_call(prompt, skills)
    if gc_call is not None:
        return gc_call
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
    if "web.act" in available_tools:
        # "book me a flight to Riyadh on Friday": an errand with no site named. Never a model
        # refusal: find sites that can do it and ask which one to use, or ask for the address.
        errand_call = site_errand_call(prompt, available_tools)
        if errand_call is not None:
            return errand_call
    if any(skill.id == "anum.external-action" for skill in skills):
        return ToolCall(
            name="external.action",
            arguments={"action": prompt, "planned_response": planned_text},
        )
    if any(skill.id == "anum.tyre-planning" for skill in skills):
        # Read-only tyre tools; the tool and its arguments come from the user's own words.
        from .tyre.tools import choose_call

        return choose_call(prompt)
    if any(skill.id == "anum.browser-research" for skill in skills):
        # The address comes from the user's own prompt, never retrieved text or model output
        # (threat model G5); the tool checks it again (docs/browser-research.md).
        address = first_https_address(prompt)
        if address is not None:
            return ToolCall(name="web.browse_page", arguments={"url": address})
    if any(skill.id == "anum.web-search" for skill in skills):
        # The query is the user's own prompt, never retrieved text or model output
        # (threat model G5); the tool caps its length.
        return ToolCall(name="web_search", arguments={"query": " ".join(prompt.split())[:400]})
    return ToolCall(name="anum.respond", arguments={"text": planned_text})


# ``web_search`` arguments for a website errand without a site (web_search.py answers with options).
FIND_SITE = "find_site"
_ARABIC_LETTER = re.compile(r"[\u0600-\u06FF]")


def _is_arabic(text: str) -> bool:
    return bool(_ARABIC_LETTER.search(text or ""))


def site_errand_call(prompt: str, available_tools: Collection[str]) -> ToolCall | None:
    """For a booking, buying or form errand that names no site (docs/action-browser.md#how-it-is-used):
    a ``web_search`` for sites that can do it, answered with 2 to 4 options and the question which
    to use, or, without web search, one sentence asking for the site's address. Never a site chosen
    by ANUM: the owner names it, and the follow-up with its https address plans ``web.act``."""
    from .capabilities import errand_goal, errand_query, site_errand

    if not site_errand(prompt):
        return None
    arabic = _is_arabic(prompt)
    goal = errand_goal(prompt)
    if "web_search" in available_tools:
        return ToolCall(
            name="web_search",
            arguments={"query": errand_query(prompt), "purpose": FIND_SITE, "goal": goal, "language": "ar" if arabic else "en"},
        )
    return ToolCall(name="anum.respond", arguments={"text": ask_site_text(goal, arabic=arabic), "errand": True})


def ask_site_text(goal: str, *, arabic: bool = False) -> str:
    """One short sentence asking for the site (web search is off here)."""
    if arabic:
        return (f"أي موقع يستخدم ANUM؟ أرسل الطلب مع عنوان https للموقع، مثلا: {goal} على https://www.example.com، "
                "فينفذه ANUM في متصفح الإجراءات ويتوقف لموافقتك قبل أن يدفع أو يحجز.")
    return (f"Which site should ANUM use? Send the request with the site's https address, for example: {goal} on "
            "https://www.example.com, and ANUM does it in the Action browser, stopping for your approval before it pays or books.")


def act_off_text(reason: str, *, arabic: bool = False) -> str:
    """The action browser is off here: the tool's own sentence (which switch, who turns it on)."""
    if arabic:
        return (f"ينفذ ANUM هذا في متصفح الإجراءات، وهو غير متاح الآن: {reason} "
                "عندما يعمل، أرسل الطلب مع عنوان https للموقع وسيتوقف ANUM لموافقتك قبل أن يدفع أو يحجز.")
    return (f"ANUM does this in the Action browser, which is not available yet. {reason} "
            "Once it is on, send the request with the site's https address and ANUM stops for your approval before it pays or books.")


def _action_browser_call(prompt: str) -> ToolCall | None:
    """``web.act`` with the scope the owner approves, or None without an https address."""
    import re as _re

    from .action_browser.handles import prompt_addresses, prompt_sites

    addresses = prompt_addresses(prompt)
    if not addresses:
        return None
    lowered = prompt.lower()
    named_cards = sorted({match.group(1) for match in _re.finditer(r"\bcard:([a-z0-9][a-z0-9-]{0,39})\b", lowered)}
                         | {match.group(1) for match in _re.finditer(r"\b([a-z0-9][a-z0-9-]{0,39})\s+card\b", lowered)})
    any_card = bool(_re.search(r"\b(card|pay|payment|checkout)\b|بطاقة|ادفع|الدفع", lowered))
    return ToolCall(
        name="web.act",
        arguments={
            "goal": " ".join(prompt.split())[:1000], "start_url": addresses[0][:2048], "allowed_domains": prompt_sites(prompt),
            # Filled from the saved logins and cards before the call is checkpointed (``action_scope``).
            "logins": {}, "cards": [], "card_names": named_cards[:5], "any_card": any_card,
        },
    )


def _green_concrete_call(prompt: str, skills: Sequence[SkillManifest]) -> ToolCall | None:
    """A Green Concrete role skill's one read tool, with the period from the user's own words."""
    if not any(skill.id.startswith("gc.") for skill in skills):
        return None
    from .green_concrete_kit import choose_call

    return choose_call(prompt, [skill.id for skill in skills])


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
