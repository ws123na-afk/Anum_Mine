"""The Brevo planner (docs/brevo.md#planner): free-text requests become a campaign draft
or a read through the runtime's tools, never a send; sending still waits for an owner's
approval bound to the payload hash; without a working connection the planner refuses."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from test_brevo import BASE, CONTEXT, MEMBER, OWNER, TENANT, VIEWER, WORKSPACE, FakeBrevo, approve, client, connect, ok
from test_brevo import brevo  # noqa: F401 (autouse fixture: fake Brevo, empty store)

from anum_api import main
from anum_api.agent_planning import AgentPlanner, choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, ToolPolicy
from anum_api.brevo import tools as brevo_tools
from anum_api.brevo.planning import AS_SOON_AS_APPROVED, PLANNED_TOOLS, plan_call
from anum_api.brevo.service import CAMPAIGNS_TOOL, DRAFT_TOOL, LISTS_TOOL, REPORT_TOOL, SEND_TOOL
from anum_api.model_gateway import MockModelGateway
from anum_api.schemas import Task, TaskStatus, WorkspaceApprovalPolicy

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
NEWSLETTER = "send the winter newsletter to the customers list"


def task(prompt: str) -> Task:
    return Task(id="task_brevo_plan", title=prompt[:40], prompt=prompt, status=TaskStatus.CREATED, tenant_id=TENANT,
                workspace_id=WORKSPACE, created_by="member_brevo", created_at=NOW, updated_at=NOW)


def plan(prompt: str) -> Any:
    planner = AgentPlanner(MockModelGateway(), default_skill_registry(), main.tool_registry)
    return asyncio.run(planner.plan(task(prompt)))


def add_customers(fake: FakeBrevo) -> None:
    fake.lists[11] = {"id": 11, "name": "Customers", "uniqueSubscribers": 350, "totalBlacklisted": 0, "folderId": 1}


# -- intent to plan ---------------------------------------------------------------------------------


def test_a_newsletter_request_plans_a_campaign_draft() -> None:
    call = plan_call(NEWSLETTER, now=NOW)
    assert call is not None and call.name == DRAFT_TOOL
    args = call.arguments
    assert args["list_names"] == ["customers"] and args["name"] == "Winter newsletter"
    assert args["subject"] == "Our winter newsletter" and "winter" in args["preview_text"]
    assert len(args["outline"]) == 4 and args["outline"][0].startswith("Opening")
    assert args["schedule"] == AS_SOON_AS_APPROVED
    scheduled = plan_call("schedule the winter newsletter to the customers and VIP lists tomorrow at 9am", now=NOW)
    assert scheduled is not None and scheduled.arguments["list_names"] == ["customers", "VIP"]
    assert scheduled.arguments["schedule"] == "2026-10-19T09:00:00+00:00"
    on_date = plan_call("draft the spring newsletter to the list named Members on 2026-11-02 14:30", now=NOW)
    assert on_date is not None and on_date.arguments["list_names"] == ["Members"]
    assert on_date.arguments["schedule"] == "2026-11-02T14:30:00+00:00"
    # A time already past is not a schedule.
    assert plan_call("send the newsletter to the customers list on 2026-01-01 09:00", now=NOW).arguments["schedule"] == AS_SOON_AS_APPROVED  # type: ignore[union-attr]


def test_reads_are_planned_as_safe_tool_calls() -> None:
    report = plan_call("how did newsletter campaign 101 perform?", now=NOW)
    assert report is not None and report.name == REPORT_TOOL and report.arguments == {"campaign_id": 101}
    reports = plan_call("show me the Brevo campaign reports", now=NOW)
    assert reports is not None and reports.name == CAMPAIGNS_TOOL and reports.arguments["status"] == "sent"
    sizes = plan_call("how many subscribers are on our lists?", now=NOW)
    assert sizes is not None and sizes.name == LISTS_TOOL
    recent = plan_call("recent newsletters in brevo", now=NOW)
    assert recent is not None and recent.name == CAMPAIGNS_TOOL
    registry = main.tool_registry
    for call in (report, reports, sizes, recent):
        definition = registry.definition(call.name)
        assert definition is not None and definition.action_kind == "read"
        assert ToolPolicy().evaluate(call, definition, CONTEXT).outcome.value == "allow"
    assert plan_call("post the winter offer on instagram", now=NOW) is None


def test_the_planner_chooses_brevo_through_the_skill_and_the_registry(brevo: FakeBrevo) -> None:  # noqa: F811
    connect()
    result = plan(NEWSLETTER)
    assert "anum.brevo" in result.plan.skill_ids
    assert [call.name for call in result.plan.tool_calls] == [DRAFT_TOOL]
    # Chat and the command preview choose the same call.
    selected = default_skill_registry().select(NEWSLETTER, main.tool_registry.names)
    assert choose_tool_call(NEWSLETTER, selected, "", main.tool_registry.names).name == DRAFT_TOOL
    # Without the Brevo tools registered (or allowed), the skill is not selectable.
    assert "anum.brevo" not in {skill.id for skill in default_skill_registry().select(NEWSLETTER, {"anum.respond", "external.action"})}


def test_a_draft_without_a_named_list_asks_which_list() -> None:
    call = plan_call("write the winter newsletter", now=NOW)
    assert call is not None and call.name == "anum.respond" and "Which Brevo contact list" in call.arguments["text"]


# -- sending stays approval-gated -----------------------------------------------------------------


def test_the_planner_never_plans_a_send() -> None:
    prompts = [NEWSLETTER, "send the newsletter now to the customers list", "send brevodraft_0123456789abcdef to everyone",
               "أرسل النشرة إلى المشتركين", "schedule the brevo newsletter for tomorrow", "send it to all subscribers"]
    for prompt in prompts:
        call = plan_call(prompt, now=NOW)
        assert call is None or call.name != SEND_TOOL, prompt
        assert call is None or call.name in PLANNED_TOOLS or call.name == "anum.respond", prompt
    existing = plan_call("send brevodraft_0123456789abcdef to everyone", now=NOW)
    assert existing is not None and existing.name == "anum.respond" and "approval" in existing.arguments["text"]


def test_a_planned_draft_sends_nothing_and_its_send_waits_for_an_owner(brevo: FakeBrevo) -> None:  # noqa: F811
    connect()
    add_customers(brevo)
    call = plan_call(NEWSLETTER, now=NOW)
    assert call is not None
    result = asyncio.run(brevo_tools.draft_campaign(call, CONTEXT))
    assert result.status == "succeeded" and "Nothing was sent" in result.summary, result.summary
    assert result.output["lists"] == ["Customers"] and result.output["requested_schedule"] == AS_SOON_AS_APPROVED
    assert not brevo.calls("POST", "/emailCampaigns") and not brevo.sent_now
    draft = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))
    assert draft["status"] == "draft" and draft["source"] == "assistant" and draft["list_ids"] == [11]
    assert draft["subject"] == "Our winter newsletter" and "<h2>Opening</h2>" in draft["html"]
    # Sending is asked for, then waits for an owner's approval bound to the payload hash.
    pending = ok(client.post(f"{BASE}/drafts/{draft['id']}/send", headers=MEMBER, json={}))
    approval = pending["approval"]
    assert approval["action"] == SEND_TOOL and approval["risk_level"] == "high" and not brevo.sent_now
    wrong = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "0" * 64})
    assert wrong.status_code >= 400 and not brevo.sent_now
    assert approve(approval, MEMBER).status_code == 403 and not brevo.sent_now
    assert approve(approval).status_code == 200 and len(brevo.sent_now) == 1
    # The policy keeps the send asking whatever the workspace's autonomy says.
    send = main.tool_registry.definition(SEND_TOOL)
    relaxed = WorkspaceApprovalPolicy()
    decision = ToolPolicy().evaluate(ToolCall(name=SEND_TOOL), send, CONTEXT, relaxed)
    assert decision.outcome.value == "require_approval" and decision.lowerable is False


def test_a_draft_names_only_lists_that_exist(brevo: FakeBrevo) -> None:  # noqa: F811
    connect()
    call = plan_call(NEWSLETTER, now=NOW)
    assert call is not None
    result = asyncio.run(brevo_tools.draft_campaign(call, CONTEXT))
    assert result.status == "failed" and "'customers'" in result.summary
    assert not brevo.calls("POST", "/emailCampaigns")


# -- not configured ---------------------------------------------------------------------------------


def test_the_planner_refuses_when_brevo_is_not_connected(brevo: FakeBrevo) -> None:  # noqa: F811
    for prompt in (NEWSLETTER, "how many subscribers are on our lists?"):
        result = plan(prompt)
        [call] = result.plan.tool_calls
        assert call.name == "anum.respond" and "Brevo is not connected" in call.arguments["text"], prompt
    assert not brevo.requests
    # A refused key is a refusal too, with its own fix.
    connect()
    brevo.key_ok = False
    assert client.post(f"{BASE}/connection/check", headers=OWNER).status_code in {200, 409, 502}
    [call] = plan(NEWSLETTER).plan.tool_calls
    assert call.name == "anum.respond" and "refused the saved API key" in call.arguments["text"]
