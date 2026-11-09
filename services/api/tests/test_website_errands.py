"""A website errand that names no site ("book me a flight to Riyadh on Friday") never ends in a
model refusal (docs/action-browser.md#how-it-is-used, docs/command.md#how-the-plan-is-made).

The owner asked Command Center to "book a ticker" and got a generic chatbot answer ("I can't
book tickets myself, since I don't have access to booking systems"). Root cause: without an
https address the action browser skill is not selected, so the request fell through to
``anum.respond`` and the task planner model, which had no list of what ANUM can do. Now:

* with web search: a ``web_search`` for sites, answered with 2 to 4 real, dated options and the
  question which to use; the follow-up with the chosen https address plans ``web.act``;
* without web search: one sentence asking for the site's address;
* with the action browser off: the tool's own sentence naming the switch that is off;
* a text answer from the task planner gets the live capability statement.

Web search is mocked (``httpx.MockTransport``); nothing here calls the internet.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from action_browser_fakes import FakeActionService
from anum_api import capabilities as caps
from anum_api import main
from anum_api.action_browser import ACTION_INTEGRATION_ID
from anum_api.action_browser.tool import (
    DISABLED_TEXT,
    UNAVAILABLE_MODE,
    UNAVAILABLE_SERVICE,
    UNAVAILABLE_SWITCH,
    ActionBrowserTool,
    ActionServiceClient,
    act_definition,
)
from anum_api.agent_planning import FIND_SITE, AgentPlanner, choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, default_tool_registry
from anum_api.commands import memory_command_store
from anum_api.conversations import conversation_store
from anum_api.integrations import IntegrationConfiguration
from anum_api.model_gateway import ModelCallMetadata, ModelResponse, ModelUsage
from anum_api.runtime import AgentRuntime
from anum_api.schemas import Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.store import store
from anum_api.voice import voice_model_gateway
from anum_api.web_search import SEARXNG_INTEGRATION_ID, SearxngWebSearch, site_options, site_options_text

TENANT = "tenant_errands"
WORKSPACE = "workspace_errands"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_errands", "x-user-roles": "owner,member"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_errands", roles=["owner", "member"])
TOKEN = "action-browser-token-for-tests-only-example"
TODAY = date(2026, 11, 9)

client = TestClient(main.app)

SEARX_RESULTS = {
    "results": [
        {"url": "https://www.flyexample.com/riyadh", "title": "Fly Example: Dubai to Riyadh", "content": "Fares from AED 450"},
        {"url": "https://www.flyexample.com/deals", "title": "Fly Example deals", "content": "Same site again"},
        {"url": "http://plain.example.net/riyadh", "title": "Not https", "content": "Dropped"},
        {"url": "https://book.airexample.ae/ruh", "title": "Air Example — book Riyadh", "content": "Friday flights"},
        {"url": "https://travel.example.org/flights", "title": "Travel Example", "content": "Compare fares"},
        {"url": "https://tickets.example.sa/ruh", "title": "Tickets Example", "content": "Riyadh"},
        {"url": "https://fifth.example.com/ruh", "title": "Fifth", "content": "More than four"},
    ]
}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def reset() -> None:
        conversation_store.clear()
        caps.clear_cache()
        memory_command_store.clear()
        main.app.dependency_overrides.clear()
        main.integration_registry.memory_configurations.clear()
        for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
            collection.clear()

    reset()
    yield
    reset()


def searx(queries: list[str], payload: dict[str, Any] = SEARX_RESULTS) -> SearxngWebSearch:
    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["q"])
        return httpx.Response(200, json=payload)

    search = SearxngWebSearch(base_url="http://searxng:8080", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    search._today = lambda: TODAY
    return search


def act_tool(*, problem: str | None = None, action_mode: bool = True, enabled: bool = True) -> ActionBrowserTool:
    service = FakeActionService()
    service.no_action_mode = not action_mode
    fake = ActionServiceClient("http://browser:8090", TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(service)))
    return ActionBrowserTool(
        problem=problem, client=None if problem else fake, planner_factory=lambda _context: None,  # type: ignore[arg-type,return-value]
        enabled=lambda _context: enabled,
    )


class FixedGateway:
    provider = "fake"
    model = "fake-model-1"

    def __init__(self, text: str = "I can't book tickets myself, since I don't have access to booking systems.") -> None:
        self.text = text
        self.prompts: list[str] = []

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        return ModelResponse(
            text=self.text,
            usage=ModelUsage(input_tokens=1, output_tokens=1, provider=self.provider, model=self.model),
            metadata=ModelCallMetadata(latency_ms=1, finish_reason="stop"),
        )

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None):  # type: ignore[no-untyped-def]
        yield "should not be used"


def registry(*, search: SearxngWebSearch | None = None, act: ActionBrowserTool | None = None):  # type: ignore[no-untyped-def]
    tools = default_tool_registry(web_search_handler=search)
    tools.register(act_definition(), act or act_tool())
    return tools


def task(prompt: str) -> Task:
    now = utc_now()
    return Task(
        id=new_id("task"), tenant_id=TENANT, workspace_id=WORKSPACE, created_by="owner_errands", title=prompt[:80],
        prompt=prompt, status=TaskStatus.CREATED, created_at=now, updated_at=now,
    )


def plan(prompt: str, tools: Any, gateway: FixedGateway | None = None) -> ToolCall:
    planner = AgentPlanner(gateway or FixedGateway(), default_skill_registry(), tools)
    return asyncio.run(planner.plan(task(prompt))).plan.tool_calls[0]


# -- routing: which call a request plans --------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "arabic"),
    [
        ("book me a ticker to Riyadh on Friday", False),
        ("Book me a flight to Riyadh on Friday", False),
        ("Can you get me a ticker to Riyadh?", False),
        ("I would like to book a hotel in Dubai for two nights", False),
        ("احجز لي تذكرة إلى الرياض يوم الجمعة", True),
        ("أبغى أحجز فندق في دبي", True),
    ],
)
def test_an_errand_without_a_site_plans_a_site_search(prompt: str, arabic: bool) -> None:
    names = registry(search=searx([])).names
    call = choose_tool_call(prompt, default_skill_registry().select(prompt, names), "I can't book tickets.", names)
    assert call.name == "web_search" and call.arguments["purpose"] == FIND_SITE
    assert call.arguments["language"] == ("ar" if arabic else "en")
    assert "ticker" not in call.arguments["query"] and "ticker" not in call.arguments["goal"]
    assert "I can't" not in json.dumps(call.arguments)


def test_the_typo_is_read_from_context_not_remarked_on() -> None:
    names = registry(search=searx([])).names
    call = choose_tool_call("book me a ticker to Riyadh on Friday", [], "", names)
    assert call.arguments["query"] == "book a ticket to Riyadh on Friday online"
    assert call.arguments["goal"] == "book me a ticket to Riyadh on Friday"


def test_an_errand_with_its_https_address_plans_web_act() -> None:
    names = registry(search=searx([])).names
    prompt = "book me a flight to Riyadh on Friday on https://www.flyexample.com/riyadh"
    call = choose_tool_call(prompt, default_skill_registry().select(prompt, names), "", names)
    assert call.name == "web.act" and call.arguments["start_url"] == "https://www.flyexample.com/riyadh"
    arabic = "احجز لي تذكرة إلى الرياض يوم الجمعة على https://www.flyexample.com"
    assert choose_tool_call(arabic, default_skill_registry().select(arabic, names), "", names).name == "web.act"


def test_without_web_search_the_reply_asks_for_the_site_in_one_sentence() -> None:
    names = registry().names
    call = choose_tool_call("book me a flight to Riyadh on Friday", [], "I can't book.", names)
    assert call.name == "anum.respond" and call.arguments["errand"] is True
    text = call.arguments["text"]
    assert text.startswith("Which site should ANUM use?") and "https://" in text and "approval before it pays" in text
    assert text.count(".") <= 3 and "can't" not in text
    arabic = choose_tool_call("احجز لي رحلة إلى الرياض يوم الجمعة", [], "", names).arguments["text"]
    assert arabic.startswith("أي موقع يستخدم ANUM؟") and "لموافقتك" in arabic


@pytest.mark.parametrize(
    "prompt",
    ["What is a good book to read?", "order status for 123", "check the status of my order", "book a table on emirates.com"],
)
def test_not_a_site_search(prompt: str) -> None:
    names = registry(search=searx([])).names
    call = choose_tool_call(prompt, default_skill_registry().select(prompt, names), "", names)
    assert call.arguments.get("purpose") != FIND_SITE


# -- the action browser switches ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "sentence"),
    [
        (lambda: act_tool(problem=UNAVAILABLE_SERVICE), "ANUM_BROWSER_RESEARCH_URL"),
        (lambda: act_tool(problem=UNAVAILABLE_SWITCH), "ANUM_ACTION_BROWSER_ENABLED=true"),
        (lambda: act_tool(action_mode=False), "ANUM_BROWSER_ACTION_MODE=1"),
        (lambda: act_tool(enabled=False), "A workspace owner turns it on under Integrations, Action browser"),
    ],
)
def test_a_switch_that_is_off_is_named_instead_of_a_refusal(tool: Any, sentence: str) -> None:
    for prompt in ("book me a flight to Riyadh on Friday", "book me a flight on https://www.flyexample.com"):
        call = plan(prompt, registry(search=searx([]), act=tool()))
        assert call.name == "anum.respond", prompt
        assert sentence in call.arguments["text"] and "can't" not in call.arguments["text"]
        assert call.arguments["text"].startswith("ANUM does this in the Action browser, which is not available yet.")
    arabic = plan("احجز لي رحلة إلى الرياض", registry(act=tool()))
    assert arabic.arguments["text"].startswith("ينفذ ANUM هذا في متصفح الإجراءات") and sentence in arabic.arguments["text"]


def test_the_unavailable_sentences_name_the_switches() -> None:
    assert "ANUM_BROWSER_ACTION_MODE=1" in UNAVAILABLE_MODE and "ANUM_ACTION_BROWSER_ENABLED=true" in UNAVAILABLE_SWITCH
    assert "Integrations, Action browser" in DISABLED_TEXT


def test_with_everything_on_the_errand_searches_and_the_url_follow_up_asks_for_the_scope() -> None:
    queries: list[str] = []
    tools = registry(search=searx(queries), act=act_tool())
    call = plan("book me a ticker to Riyadh on Friday", tools)
    assert call.name == "web_search" and call.arguments["purpose"] == FIND_SITE
    result = asyncio.run(tools.execute(call, CONTEXT))
    assert result.status == "succeeded" and queries == ["book a ticket to Riyadh on Friday online"]
    assert result.summary.splitlines()[0] == "Sites where this can be done, from a web search on 9 Nov 2026:"
    follow_up = plan("book me a ticket to Riyadh on Friday on https://www.flyexample.com/riyadh", tools)
    assert follow_up.name == "web.act" and follow_up.arguments["allowed_domains"] == ["flyexample.com"]


# -- the options -------------------------------------------------------------------------------


def test_options_are_two_to_four_real_https_sites_dated_with_the_question() -> None:
    options = site_options([{"title": item["title"], "url": item["url"], "snippet": ""} for item in SEARX_RESULTS["results"]])
    assert [item["site"] for item in options] == ["flyexample.com", "book.airexample.ae", "travel.example.org", "tickets.example.sa"]
    text = site_options_text("book me a ticket to Riyadh on Friday", options, arabic=False, today=TODAY)
    lines = text.splitlines()
    assert lines[0] == "Sites where this can be done, from a web search on 9 Nov 2026:"
    assert lines[1] == "1. Fly Example: Dubai to Riyadh: https://www.flyexample.com/riyadh"
    assert lines[2].startswith("2. Air Example, book Riyadh")  # long dashes from a title become plain
    assert lines[5] == "Which one should ANUM use?"
    assert "book me a ticket to Riyadh on Friday on https://www.flyexample.com/riyadh" in lines[6]
    assert "approval before it pays or books" in lines[6]
    assert "—" not in text and "–" not in text
    arabic = site_options_text("احجز لي تذكرة إلى الرياض", options, arabic=True, today=TODAY)
    assert "2026-11-09" in arabic and "أي موقع يستخدم ANUM؟" in arabic and "https://www.flyexample.com/riyadh" in arabic


def test_no_results_never_invents_a_site() -> None:
    tools = registry(search=searx([], {"results": []}), act=act_tool())
    call = plan("book me a flight to Riyadh on Friday", tools)
    result = asyncio.run(tools.execute(call, CONTEXT))
    assert result.status == "succeeded" and result.output["results"] == []
    assert result.summary.startswith("A web search on 9 Nov 2026 found no site for this. Which site should ANUM use?")


# -- the capability statement in the task planner's text answer --------------------------------


def test_a_text_answer_gets_the_live_capability_statement() -> None:
    gateway = FixedGateway("ANUM can do that as a task.")
    tools = registry(search=searx([]), act=act_tool())
    runtime = AgentRuntime(gateway, main.repository, tools=tools)  # type: ignore[arg-type]
    created = task("what can you help me with for my trip next week?")
    blocks = runtime._capability_block(created, CONTEXT)
    assert blocks is not None
    on, off = caps.capability_lines(caps.workspace_capabilities(CONTEXT, tools=tools.names))
    assert blocks.startswith(caps.CAPABILITY_HEADER) and all(line in blocks for line in on + off)
    assert "Action browser" in blocks and "never describe yourself as a chatbot" in blocks
    asyncio.run(runtime.planner.plan(created, capabilities=blocks))
    assert gateway.prompts[-1].startswith(caps.CAPABILITY_HEADER)
    assert gateway.prompts[-1].rstrip().endswith("Task:\nwhat can you help me with for my trip next week?")
    # A task a tool answers (the site search) does not need it.
    assert runtime._capability_block(task("book me a flight to Riyadh on Friday"), CONTEXT) is None


# -- end to end: Command Center and the Assistant chat -----------------------------------------


@pytest.fixture
def action_browser_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator set up the browser service in action mode and an owner switched it on."""
    from anum_api.settings import settings

    monkeypatch.setattr(settings, "browser_research_url", "http://browser:8090")
    monkeypatch.setattr(settings, "browser_research_token", TOKEN)
    monkeypatch.setattr(settings, "action_browser_enabled", True)
    handler = main.tool_registry.handler("web.act")
    fake = act_tool()
    monkeypatch.setattr(handler, "problem", None)
    monkeypatch.setattr(handler, "client", fake.client)
    main.integration_registry.configure(ACTION_INTEGRATION_ID, CONTEXT, IntegrationConfiguration(enabled=True))


@pytest.fixture
def web_search_on(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queries: list[str] = []
    tools = default_tool_registry(web_search_handler=searx(queries))
    monkeypatch.setitem(main.tool_registry._definitions, "web_search", tools.definition("web_search"))
    monkeypatch.setitem(main.tool_registry._handlers, "web_search", tools.handler("web_search"))
    main.integration_registry.configure(SEARXNG_INTEGRATION_ID, CONTEXT, IntegrationConfiguration(enabled=True))
    return queries


def command_result(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    preview = client.post("/api/v1/commands/preview", headers=OWNER, json={"text": text})
    assert preview.status_code == 200, preview.text
    (step,) = preview.json()["steps"]
    created = client.post(
        "/api/v1/commands", headers=OWNER, json={"text": text, "steps": [{"instruction": step["instruction"], "depends_on": []}]}
    )
    assert created.status_code == 201, created.text
    command = created.json()
    ran = client.post(f"/api/v1/tasks/{command['steps'][0]['task_id']}/run", headers=OWNER)
    assert ran.status_code == 200, ran.text
    view = client.get(f"/api/v1/commands/{command['id']}", headers=OWNER).json()
    return step, view


def test_command_center_says_which_switch_is_off() -> None:
    step, view = command_result("Book me a flight to Riyadh on Friday")
    assert step["agent"]["id"] == "action" and step["tool"]["name"] == "anum.respond"
    assert step["note"].startswith("No site named")
    assert view["status"] == "completed"
    result = view["steps"][0]["result"]
    assert "Action browser, which is not available yet" in result and "ANUM_BROWSER_RESEARCH_URL" in result
    assert "can't" not in result and "don't have access" not in result


def test_command_center_asks_for_the_site_when_search_is_off(action_browser_on: None) -> None:
    _step, view = command_result("Book me a flight to Riyadh on Friday")
    assert view["steps"][0]["result"].startswith("Which site should ANUM use?")


def test_command_center_lists_sites_when_search_is_on(action_browser_on: None, web_search_on: list[str]) -> None:
    step, view = command_result("Book me a ticker to Riyadh on Friday")
    assert step["agent"]["id"] == "research" and step["tool"]["name"] == "web_search"
    assert step["approval"] == "none" and "searches the web for sites" in step["note"]
    result = view["steps"][0]["result"]
    assert result.startswith("Sites where this can be done, from a web search on")
    assert "https://www.flyexample.com/riyadh" in result and "Which one should ANUM use?" in result
    assert web_search_on == ["Book a ticket to Riyadh on Friday online"]
    arabic, arabic_view = command_result("احجز لي تذكرة إلى الرياض يوم الجمعة")
    assert arabic["note"].startswith("لم يذكر موقع: تبحث هذه الخطوة في الويب")
    assert "أي موقع يستخدم ANUM؟" in arabic_view["steps"][0]["result"]


def frames(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    assert response.status_code == 200, response.text
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((fields["event"], json.loads(fields["data"])))
    return parsed


def test_chat_lists_sites_for_a_booking_without_a_site(action_browser_on: None, web_search_on: list[str]) -> None:
    gateway = FixedGateway()
    main.app.dependency_overrides[voice_model_gateway] = lambda: gateway
    created = client.post("/api/v1/conversations", headers=OWNER, json={})
    stream = frames(client.post(f"/api/v1/conversations/{created.json()['id']}/ask", headers=OWNER,
                                json={"content": "book me a flight to Riyadh on Friday"}))
    cards = [data["card"] for kind, data in stream if kind == "tool"]
    assert cards and cards[-1]["state"] == "results" and cards[-1]["tool"] == "web_search"
    assert cards[-1]["summary"].startswith("Sites where this can be done")
    assert [item["url"] for item in cards[-1]["items"]][:1] == ["https://www.flyexample.com/riyadh"]
    reply = "".join(data.get("text", "") for kind, data in stream if kind == "delta")
    assert "don't have access" not in reply and "can't" not in reply


def test_chat_asks_for_the_site_when_the_owner_switched_search_off(action_browser_on: None, web_search_on: list[str]) -> None:
    main.integration_registry.configure(SEARXNG_INTEGRATION_ID, CONTEXT, IntegrationConfiguration(enabled=False))
    main.app.dependency_overrides[voice_model_gateway] = lambda: FixedGateway()
    created = client.post("/api/v1/conversations", headers=OWNER, json={})
    stream = frames(client.post(f"/api/v1/conversations/{created.json()['id']}/ask", headers=OWNER,
                                json={"content": "book me a flight to Riyadh on Friday"}))
    [card] = [data["card"] for kind, data in stream if kind == "tool"]
    assert card["state"] == "needs_input" and "https://" in card["summary"] and web_search_on == []
    assert store.tasks == {}
