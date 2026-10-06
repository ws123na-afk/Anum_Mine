import asyncio

from anum_api.agent_planning import AgentPlanner
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import (
    ToolCall,
    ToolPolicy,
    ToolPolicyOutcome,
    default_tool_registry,
)
from anum_api.model_gateway import MockModelGateway
from anum_api.schemas import Task, TaskStatus, TenantContext, utc_now


def make_context(roles: list[str] | None = None) -> TenantContext:
    return TenantContext(
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        user_id="user_a",
        roles=roles or ["owner"],
    )


def make_task(prompt: str) -> Task:
    now = utc_now()
    return Task(
        id="task_agent_modules",
        title="Agent module test",
        prompt=prompt,
        status=TaskStatus.CREATED,
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        created_at=now,
        updated_at=now,
    )


def test_skill_registry_selects_only_triggered_skills_with_available_tools() -> None:
    skills = default_skill_registry()

    selected = skills.select("Draft and publish the update", {"anum.respond"})

    assert [skill.id for skill in selected] == [
        "anum.task-planning",
        "anum.document-drafting",
    ]


def test_planner_selects_external_tool_for_high_impact_task() -> None:
    tools = default_tool_registry()
    planner = AgentPlanner(MockModelGateway(), default_skill_registry(), tools)

    result = asyncio.run(planner.plan(make_task("Send and publish the update")))

    assert result.plan.tool_calls[0].name == "external.action"
    assert "anum.external-action" in result.plan.skill_ids


def test_tool_policy_allows_internal_response() -> None:
    tools = default_tool_registry()
    call = ToolCall(name="anum.respond", arguments={"text": "ready"})

    decision = ToolPolicy(tools.names).evaluate(call, tools.definition(call.name), make_context())

    assert decision.outcome == ToolPolicyOutcome.ALLOW


def test_tool_policy_requires_approval_for_external_action() -> None:
    tools = default_tool_registry()
    call = ToolCall(name="external.action", arguments={"action": "publish"})

    decision = ToolPolicy(tools.names).evaluate(call, tools.definition(call.name), make_context())

    assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL


def test_tool_policy_blocks_unknown_tool() -> None:
    tools = default_tool_registry()
    call = ToolCall(name="unknown.tool")

    decision = ToolPolicy(tools.names).evaluate(call, tools.definition(call.name), make_context())

    assert decision.outcome == ToolPolicyOutcome.BLOCK


def test_tool_policy_blocks_external_action_without_required_role() -> None:
    tools = default_tool_registry()
    call = ToolCall(name="external.action", arguments={"action": "publish"})

    decision = ToolPolicy(tools.names).evaluate(
        call,
        tools.definition(call.name),
        make_context(["viewer"]),
    )

    assert decision.outcome == ToolPolicyOutcome.BLOCK


def test_runtime_uses_real_model_answer_as_task_result() -> None:
    import httpx

    from anum_api.model_gateway import build_model_gateway
    from anum_api.repository import InMemoryRepository
    from anum_api.runtime import AgentRuntime
    from anum_api.store import InMemoryStore

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "llama3.2",
                "choices": [{"message": {"content": "Paris is the capital of France."}, "finish_reason": "stop"}],
            },
        )

    gateway = build_model_gateway(
        "ollama", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    runtime = AgentRuntime(gateway, InMemoryRepository(InMemoryStore()))

    run, approval = asyncio.run(runtime.run_task(make_task("What is the capital of France?"), make_context()))

    assert approval is None
    assert run.status == TaskStatus.COMPLETED
    assert run.result == "Paris is the capital of France."
