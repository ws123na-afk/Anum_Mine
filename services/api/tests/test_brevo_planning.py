"""The Brevo planner (docs/brevo.md#planner): free-text requests become a campaign draft
or a read through the runtime's tools, never a send; sending still waits for an owner's
approval bound to the payload hash; without a working connection the planner refuses."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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
    assert result.output["lists"] == ["Customers"] and result.output["scheduled_at"] == AS_SOON_AS_APPROVED
    assert not brevo.calls("POST", "/emailCampaigns") and not brevo.sent_now
    draft = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))
    assert draft["status"] == "draft" and draft["source"] == "assistant" and draft["list_ids"] == [11]
    assert draft["subject"] == "Our winter newsletter" and ">Opening</h2>" in draft["html"] and "{{ unsubscribe }}" in draft["html"]
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


# -- Arabic and English: list names and dates -------------------------------------------------------


def test_arabic_requests_name_lists_and_dates() -> None:
    call = plan_call("أرسل نشرة الشتاء إلى قائمة العملاء غدا الساعة ٩ صباحا", now=NOW)
    assert call is not None and call.name == DRAFT_TOOL
    args = call.arguments
    assert args["list_names"] == ["العملاء"] and args["language"] == "ar" and args["about"] == "الشتاء"
    assert args["schedule"] == "2026-10-19T09:00:00+00:00" and args["subject"].startswith("نشرة الشتاء")
    assert args["outline"][0].startswith("الترحيب")
    two = plan_call("جهز نشرة الصيف لقائمتي العملاء والموزعين اليوم الساعة 7 مساء", now=NOW)
    assert two is not None and two.arguments["list_names"] == ["العملاء", "الموزعين"]
    assert two.arguments["schedule"] == "2026-10-18T19:00:00+00:00"
    dated = plan_call("اكتب النشرة إلى قائمة VIP بتاريخ 2026-11-02 الساعة 14:30", now=NOW)
    assert dated is not None and dated.arguments["list_names"] == ["VIP"]
    assert dated.arguments["schedule"] == "2026-11-02T14:30:00+00:00"
    asked = plan_call("جهز نشرة الشتاء", now=NOW)
    assert asked is not None and asked.name == "anum.respond" and "قائمة" in asked.arguments["text"]
    # The Arabic skill words select the planner too.
    selected = default_skill_registry().select("أرسل النشرة إلى قائمة العملاء", main.tool_registry.names)
    assert "anum.brevo" in {skill.id for skill in selected}


def test_english_requests_name_lists_and_dates() -> None:
    today = plan_call("draft the autumn newsletter to the customers list today at 6pm", now=NOW)
    assert today is not None and today.arguments["schedule"] == "2026-10-18T18:00:00+00:00" and today.arguments["language"] == "en"
    named = plan_call("prepare the newsletter for the lists called Trade Partners on 2026-12-01 at 08:15", now=NOW)
    assert named is not None and named.arguments["list_names"] == ["Trade Partners"]
    assert named.arguments["schedule"] == "2026-12-01T08:15:00+00:00"


# -- the full email: brand, Brevo template, model ---------------------------------------------------


def _templates(fake: FakeBrevo, monkeypatch: Any, templates: list[dict[str, Any]]) -> None:
    import httpx

    from anum_api.brevo import client as brevo_client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/smtp/templates":
            fake.requests.append(request)
            return httpx.Response(200, json={"templates": templates, "count": len(templates)})
        return fake.handler(request)

    monkeypatch.setattr(brevo_client, "transport", httpx.MockTransport(handler))


def _branded(monkeypatch: Any) -> None:
    from anum_api.organisations import memory_branding

    monkeypatch.setitem(memory_branding.items, TENANT, {"tenant_id": TENANT, "display_name": "Desert Tyres", "logo": None, "logo_content_type": None})


def test_the_full_email_uses_the_brand_and_the_built_in_layout(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    connect()
    add_customers(brevo)
    _branded(monkeypatch)
    result = asyncio.run(brevo_tools.draft_campaign(plan_call(NEWSLETTER, now=NOW), CONTEXT))  # type: ignore[arg-type]
    assert result.status == "succeeded" and result.output["email"] == {"generator": "template", "brevo_template_id": None, "brand": "Desert Tyres", "logo": False}
    html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
    assert "Desert Tyres" in html and "Our winter newsletter" in html and "The winter news and offers" in html
    assert "Hello from Desert Tyres" in html and "{{ unsubscribe }}" in html and 'dir="ltr"' in html
    assert "<script" not in html and "http" not in html  # nothing remote, nothing executable


def test_the_full_email_fills_the_owners_brevo_template(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    connect()
    brevo.lists[12] = {"id": 12, "name": "العملاء", "uniqueSubscribers": 80, "totalBlacklisted": 0, "folderId": 1}
    _branded(monkeypatch)
    _templates(brevo, monkeypatch, [
        {"id": 3, "name": "Old", "isActive": True, "tag": "", "htmlContent": "<html><body>no marker</body></html>"},
        {"id": 4, "name": "House style", "isActive": True, "tag": "anum",
         "htmlContent": "<html><body><header>Desert Tyres header</header>{{ params.anum_content }}<footer>{{ unsubscribe }}</footer></body></html>"},
    ])
    call = plan_call("أرسل نشرة الشتاء إلى قائمة عملاء غدا الساعة 9", now=NOW)
    assert call is not None
    result = asyncio.run(brevo_tools.draft_campaign(call, CONTEXT))
    assert result.status == "succeeded", result.summary
    assert result.output["email"]["brevo_template_id"] == 4 and result.output["lists"] == ["العملاء"]
    html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
    assert html.startswith("<html><body><header>Desert Tyres header</header>") and "{{ params.anum_content }}" not in html
    assert '<div dir="rtl">' in html and "مرحبا من Desert Tyres" in html


def test_a_connected_model_writes_the_sections_and_its_text_is_escaped(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    from anum_api.marketing import assist

    seen: list[str] = []

    class FakeModel:
        provider = "anthropic"

        async def generate_text(self, prompt: str) -> Any:
            seen.append(prompt)
            answer = {"sections": [{"heading": "Winter is here", "body": "Book your fitting <script>x</script> this week."}],
                      "button": "Book now"}
            return type("Answer", (), {"text": json.dumps(answer)})()

    monkeypatch.setattr(assist, "workspace_gateway", lambda context: FakeModel())
    connect()
    add_customers(brevo)
    _branded(monkeypatch)
    result = asyncio.run(brevo_tools.draft_campaign(plan_call(NEWSLETTER, now=NOW), CONTEXT))  # type: ignore[arg-type]
    assert result.status == "succeeded" and result.output["email"]["generator"] == "model"
    assert NEWSLETTER in seen[0] and "Business name: Desert Tyres" in seen[0]
    html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
    assert "Winter is here" in html and "&lt;script&gt;" in html and "<script>" not in html and "Book now" in html


# -- the schedule is saved on the draft -------------------------------------------------------------


def test_the_planned_schedule_is_saved_and_reused_when_asking_to_send(brevo: FakeBrevo) -> None:  # noqa: F811
    connect()
    add_customers(brevo)
    when = (datetime.now(UTC) + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    call = plan_call(f"schedule the winter newsletter to the customers list on {when:%Y-%m-%d} 09:00")
    assert call is not None and call.arguments["schedule"] == when.isoformat()
    result = asyncio.run(brevo_tools.draft_campaign(call, CONTEXT))
    assert result.output["scheduled_at"] == when.isoformat() and "once an owner approves it" in result.summary
    draft_id = result.output["draft_id"]
    assert ok(client.get(f"{BASE}/drafts/{draft_id}", headers=VIEWER))["scheduled_at"].startswith(f"{when:%Y-%m-%dT09:00:00}")
    # Asking to send without a time reuses the saved one; the approval binds it.
    pending = ok(client.post(f"{BASE}/drafts/{draft_id}/send", headers=MEMBER, json={}))
    assert pending["approval"]["arguments"]["scheduled_at"] == when.isoformat()
    # Withdrawing keeps it; asking again reuses it.
    withdrawn = ok(client.post(f"{BASE}/drafts/{draft_id}/cancel", headers=MEMBER))
    assert withdrawn["status"] == "draft" and withdrawn["scheduled_at"].startswith(f"{when:%Y-%m-%dT09:00:00}")
    again = ok(client.post(f"{BASE}/drafts/{draft_id}/send", headers=MEMBER, json={}))
    assert again["approval"]["arguments"]["scheduled_at"] == when.isoformat()
    assert approve(again["approval"]).status_code == 200 and not brevo.sent_now  # scheduled in Brevo, not sent now
    created = json.loads(brevo.calls("POST", "/emailCampaigns")[0].content)
    assert created["scheduledAt"] == when.isoformat()
    # An explicit null still means as soon as approved.
    other = asyncio.run(brevo_tools.draft_campaign(call, CONTEXT))
    now_pending = ok(client.post(f"{BASE}/drafts/{other.output['draft_id']}/send", headers=MEMBER, json={"scheduled_at": None}))
    assert now_pending["approval"]["arguments"]["scheduled_at"] == AS_SOON_AS_APPROVED


# -- the organisation logo in the email header ------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"example-logo-bytes"
PUBLIC_API = "https://api.example.com"
LOGO_PATH = "/api/v1/organization/branding/logo/public/"


def _with_logo(monkeypatch: Any, *, public_api: str = PUBLIC_API, logo: bytes | None = PNG) -> None:
    from anum_api.marketing.config import marketing_settings
    from anum_api.organisations import memory_branding

    monkeypatch.setattr(marketing_settings, "public_api_url", public_api)
    monkeypatch.setitem(memory_branding.items, TENANT, {"tenant_id": TENANT, "display_name": "Desert Tyres", "logo": logo,
                                                         "logo_content_type": "image/png" if logo else None})


def _logo_src(html: str) -> str:
    start = html.index(f'<img src="{PUBLIC_API}{LOGO_PATH}') + len('<img src="')
    return html[start:html.index('"', start)]


def test_the_email_header_shows_the_logo_through_a_long_lived_public_link(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    from anum_api import organisations

    connect()
    add_customers(brevo)
    _with_logo(monkeypatch)
    result = asyncio.run(brevo_tools.draft_campaign(plan_call(NEWSLETTER, now=NOW), CONTEXT))  # type: ignore[arg-type]
    assert result.status == "succeeded" and result.output["email"]["logo"] is True
    html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
    assert 'alt="Desert Tyres logo"' in html and 'width="160"' in html
    src = _logo_src(html)
    # The link works with no sign-in, serves only the image, and lasts about 13 months.
    served = client.get(src.removeprefix(PUBLIC_API))
    assert served.status_code == 200 and served.content == PNG and served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff" and "public" in served.headers["cache-control"]
    later = organisations.utc_now() + timedelta(days=organisations.LOGO_LINK_DAYS - 5)
    monkeypatch.setattr(organisations, "utc_now", lambda: later)
    assert client.get(src.removeprefix(PUBLIC_API)).status_code == 200
    monkeypatch.setattr(organisations, "utc_now", lambda: later + timedelta(days=10))
    assert client.get(src.removeprefix(PUBLIC_API)).status_code == 404  # expired


def test_a_tampered_link_or_a_removed_logo_is_a_plain_404(monkeypatch: Any) -> None:
    from anum_api import organisations

    _with_logo(monkeypatch)
    link = organisations.logo_link(CONTEXT)
    assert link is not None and link.startswith(f"{PUBLIC_API}{LOGO_PATH}")
    path = link.removeprefix(PUBLIC_API)
    assert client.get(path[:-4] + "abcd").status_code == 404
    assert client.get(f"{LOGO_PATH}not-a-token").status_code == 404
    _with_logo(monkeypatch, logo=None)
    assert client.get(path).status_code == 404 and organisations.logo_link(CONTEXT) is None


def test_the_logo_fills_a_template_marker_with_arabic_alt_text(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    connect()
    brevo.lists[12] = {"id": 12, "name": "العملاء", "uniqueSubscribers": 80, "totalBlacklisted": 0, "folderId": 1}
    _with_logo(monkeypatch)
    _templates(brevo, monkeypatch, [{"id": 4, "name": "House style", "isActive": True, "tag": "anum",
                                     "htmlContent": "<html><body>{{ params.anum_logo }}{{ params.anum_content }}</body></html>"}])
    result = asyncio.run(brevo_tools.draft_campaign(plan_call("أرسل نشرة الشتاء إلى قائمة العملاء", now=NOW), CONTEXT))  # type: ignore[arg-type]
    assert result.status == "succeeded", result.summary
    html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
    assert html.startswith(f'<html><body><img src="{PUBLIC_API}{LOGO_PATH}') and 'alt="شعار Desert Tyres"' in html
    assert "{{ params.anum_logo }}" not in html


def test_without_a_logo_or_a_public_address_the_name_heads_the_email(brevo: FakeBrevo, monkeypatch: Any) -> None:  # noqa: F811
    connect()
    add_customers(brevo)
    _templates(brevo, monkeypatch, [{"id": 5, "name": "House style", "isActive": True, "tag": "anum",
                                     "htmlContent": "<html><body>{{ params.anum_logo }}{{ params.anum_content }}</body></html>"}])
    for public_api, logo in (("", PNG), (PUBLIC_API, None)):
        _with_logo(monkeypatch, public_api=public_api, logo=logo)
        result = asyncio.run(brevo_tools.draft_campaign(plan_call(NEWSLETTER, now=NOW), CONTEXT))  # type: ignore[arg-type]
        assert result.status == "succeeded" and result.output["email"]["logo"] is False
        html = ok(client.get(f"{BASE}/drafts/{result.output['draft_id']}", headers=VIEWER))["html"]
        assert "<img" not in html and "{{ params.anum_logo }}" not in html
    # The built-in layout puts the brand name where the logo would be.
    from anum_api.brevo.compose import layout

    built_in = layout(subject="Our winter newsletter", preview_text="", body="", brand="Desert Tyres", rtl=False, logo_url=None)
    assert ">Desert Tyres</td>" in built_in and "<img" not in built_in
