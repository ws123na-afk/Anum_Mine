"""What ANUM can do in this workspace, and how chat and voice route on it
(docs/conversations.md#what-anum-can-do, docs/voice.md#what-anum-can-do), memory backend.

The capability list is read from the real state (registered tools, the caller's role,
connected accounts, an owner's integration switches), never invented. Chat and voice
answer "what can you do?" from it, and a request ANUM can do becomes a task or a card,
never a model refusal.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import capabilities as caps
from anum_api import chat_tools, main
from anum_api.action_browser import ACTION_INTEGRATION_ID
from anum_api.agent_skills import default_skill_registry
from anum_api.anthropic_gateway import build_messages_request, count_cache_breakpoints
from anum_api.browser_research import BROWSER_INTEGRATION_ID
from anum_api.conversations import SYSTEM_PROMPT, build_messages, conversation_store
from anum_api.integrations import IntegrationConfiguration
from anum_api.model_gateway import ModelCallMetadata, ModelResponse, ModelUsage
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.store import store
from anum_api.voice import voice_model_gateway
from anum_api.voice_assistant import WorkspaceSnapshot, voice_messages

TENANT = "tenant_caps"
WORKSPACE = "workspace_caps"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_caps", "x-user-roles": "owner"}
VIEWER = {**OWNER, "x-user-id": "viewer_caps", "x-user-roles": "viewer"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_caps", roles=["owner"])
VIEWER_CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_caps", roles=["viewer"])

client = TestClient(main.app)


class RecordingGateway:
    provider = "fake"
    model = "fake-model-1"

    def __init__(self, reply: str = "ANUM can run tasks here.") -> None:
        self.reply = reply
        self.plans: list[str] = []
        self.chats: list[list[dict[str, str]]] = []

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.plans.append(prompt)
        return ModelResponse(
            text="Planned.",
            usage=ModelUsage(input_tokens=1, output_tokens=1, provider=self.provider, model=self.model),
            metadata=ModelCallMetadata(latency_ms=1, finish_reason="stop"),
        )

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        self.chats.append([dict(message) for message in messages])
        yield self.reply


@pytest.fixture(autouse=True)
def clean() -> Any:
    def reset() -> None:
        conversation_store.clear()
        caps.clear_cache()
        main.app.dependency_overrides.clear()
        main.integration_registry.memory_configurations.clear()
        for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
            collection.clear()

    reset()
    yield
    reset()


@pytest.fixture
def browser_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator set up the browser service and turned the action browser on."""
    monkeypatch.setattr(settings, "browser_research_url", "http://browser:8090")
    monkeypatch.setattr(settings, "browser_research_token", "test-token-for-the-browser-service-example")
    monkeypatch.setattr(settings, "action_browser_enabled", True)


def switch(integration_id: str, enabled: bool) -> None:
    main.integration_registry.configure(integration_id, CONTEXT, IntegrationConfiguration(enabled=enabled))


def use(gateway: RecordingGateway) -> RecordingGateway:
    main.app.dependency_overrides[voice_model_gateway] = lambda: gateway
    return gateway


def frames(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((fields["event"], json.loads(fields["data"])))
    return parsed


def ask(question: str, headers: dict[str, str] = OWNER, language: str | None = None) -> list[tuple[str, dict]]:
    created = client.post("/api/v1/conversations", headers=headers, json={})
    assert created.status_code == 201, created.text
    extra = {"accept-language": language} if language else {}
    response = client.post(
        f"/api/v1/conversations/{created.json()['id']}/ask", headers={**headers, **extra}, json={"content": question}
    )
    return frames(response)


def cards(stream: list[tuple[str, dict]]) -> list[dict]:
    return [data["card"] for kind, data in stream if kind == "tool"]


def state(found: list[caps.Capability]) -> dict[str, bool]:
    return {item.id: item.available for item in found}


# -- the list, from fake and real workspace states --------------------------------------------


def all_on() -> list[caps.Capability]:
    return [caps.Capability(cap_id, True) for cap_id, _ in caps.CHECKS]


def all_off() -> list[caps.Capability]:
    return [caps.Capability(cap_id, False, reason="not connected: Integrations > X", reason_ar="غير مربوط") for cap_id, _ in caps.CHECKS]


def test_the_block_lists_every_capability_that_is_on() -> None:
    block = caps.capability_block(all_on())
    assert block.startswith(caps.CAPABILITY_HEADER)
    for name, *_ in caps.LABELS.values():
        assert f"- {name}:" in block
    assert "Not available yet" not in block
    assert "never say you cannot" in block


def test_the_block_says_why_each_off_capability_is_off_and_where_to_turn_it_on() -> None:
    block = caps.capability_block(all_off())
    assert "On:\n- none" in block
    assert block.count("not connected: Integrations > X") == len(caps.CHECKS)
    # One short line each: no nested parentheses from the descriptions.
    assert "((" not in block and "))" not in block


def test_a_partial_state_splits_the_list() -> None:
    found = [caps.Capability("tasks", True), caps.Capability("act", False, reason="off: a workspace owner turns it on under Integrations > Action browser", reason_ar="متوقف")]
    on, off = caps.capability_lines(found)
    assert on == ["- Tasks and agents: create, run and track tasks, hand work to agents and teammates, run quick commands"]
    assert off == ["- Action browser (do a task on a website: book, buy, fill and submit forms, download statements): off: a workspace owner turns it on under Integrations > Action browser"]
    on_ar, off_ar = caps.capability_lines(found, arabic=True)
    assert on_ar[0].startswith("- المهام والوكلاء:") and off_ar[0].startswith("- متصفح الإجراءات") and off_ar[0].endswith("متوقف")


def test_without_a_task_runtime_nothing_tool_backed_is_on() -> None:
    found = caps.read_capabilities(CONTEXT, set())
    assert state(found)["tasks"] is False
    assert {cap_id for cap_id, on in state(found).items() if on} <= {"approvals", "calendar", "files", "reports", "meetings", "automations"}
    tasks = caps.by_id(found, "tasks")
    assert tasks is not None and "not available on this server" in (tasks.reason or "")


def test_the_real_state_of_a_fresh_workspace() -> None:
    found = caps.read_capabilities(CONTEXT, main.tool_registry.names)
    on = state(found)
    assert on["tasks"] and on["approvals"] and on["files"] and on["reports"]
    # Nothing connected in a fresh workspace, and the browsers are off until set up and switched on.
    assert not on["whatsapp"] and not on["commerce"] and not on["browse"] and not on["act"]
    act = caps.by_id(found, "act")
    assert act is not None and "not set up on this server" in (act.reason or "")
    assert "غير مجهز" in (act.reason_ar or "")


def test_a_viewer_is_told_their_role_is_the_reason() -> None:
    found = caps.read_capabilities(VIEWER_CONTEXT, main.tool_registry.names)
    tasks = caps.by_id(found, "tasks")
    assert tasks is not None and not tasks.available and "Settings > Members" in (tasks.reason or "")


def test_the_action_browser_follows_the_owners_switch(browser_service: None) -> None:
    act = caps.by_id(caps.read_capabilities(CONTEXT, main.tool_registry.names), "act")
    assert act is not None and not act.available
    assert act.reason == "off: a workspace owner turns it on under Integrations > Action browser"
    switch(ACTION_INTEGRATION_ID, True)
    switch(BROWSER_INTEGRATION_ID, True)
    on = state(caps.read_capabilities(CONTEXT, main.tool_registry.names))
    assert on["act"] and on["browse"]


def test_the_list_is_cached_briefly_per_caller(browser_service: None) -> None:
    clock = [100.0]
    first = caps.workspace_capabilities(CONTEXT, tools=main.tool_registry.names, now=lambda: clock[0])
    switch(ACTION_INTEGRATION_ID, True)
    assert state(caps.workspace_capabilities(CONTEXT, tools=main.tool_registry.names, now=lambda: clock[0]))["act"] == state(first)["act"] is False
    clock[0] += caps.CACHE_SECONDS + 1
    assert state(caps.workspace_capabilities(CONTEXT, tools=main.tool_registry.names, now=lambda: clock[0]))["act"] is True
    # Another person (another role) is read on its own.
    assert state(caps.workspace_capabilities(VIEWER_CONTEXT, tools=main.tool_registry.names))["tasks"] is False


def test_a_module_that_cannot_be_read_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_context: TenantContext, _tools: Any) -> caps.Capability:
        raise RuntimeError("store down")

    monkeypatch.setattr(caps, "CHECKS", [("email", broken), *[item for item in caps.CHECKS if item[0] != "email"]])
    email = caps.by_id(caps.read_capabilities(CONTEXT, main.tool_registry.names), "email")
    assert email is not None and not email.available and "could not be read" in (email.reason or "")


# -- the prompts ----------------------------------------------------------------------------------


def test_the_chat_prompt_speaks_as_anum_and_never_says_it_has_no_tools() -> None:
    assert "Speak as ANUM" in SYSTEM_PROMPT and "governed tasks" in SYSTEM_PROMPT
    assert "You have no tools" not in SYSTEM_PROMPT and "suggest asking" not in SYSTEM_PROMPT


def test_what_can_you_do_reaches_the_model_with_the_live_list() -> None:
    gateway = use(RecordingGateway())
    stream = ask("What can you do?")
    assert cards(stream) == [] and store.tasks == {}
    system = [message for message in gateway.chats[0] if message["role"] == "system"]
    # The static prompt, ended by the date line (current_time.py), then the live list.
    assert system[0]["content"].startswith(SYSTEM_PROMPT) and "Current date and time:" in system[0]["content"]
    assert caps.CAPABILITY_HEADER not in system[0]["content"]
    assert system[1]["content"].startswith(caps.CAPABILITY_HEADER)
    assert "- Tasks and agents:" in system[1]["content"] and "Action browser" in system[1]["content"]


def test_an_arabic_question_gets_the_same_list() -> None:
    gateway = use(RecordingGateway(reply="أستطيع عبر ANUM تشغيل المهام."))
    ask("ماذا تستطيع أن تفعل؟")
    assert caps.asks_what_anum_can_do("ماذا تستطيع أن تفعل؟")
    assert any(message["content"].startswith(caps.CAPABILITY_HEADER) for message in gateway.chats[0])


def test_the_capability_part_stays_out_of_the_cached_system_prefix() -> None:
    messages = build_messages([], "What can you do?", capabilities=caps.capability_block(all_on()))
    body = build_messages_request(messages, model="claude-test", max_tokens=100)
    assert [("cache_control" in block) for block in body["system"]] == [True, False]
    assert body["system"][1]["text"].startswith(caps.CAPABILITY_HEADER)
    assert count_cache_breakpoints(body) == 2
    # Without it the request is exactly as before.
    plain = build_messages_request(build_messages([], "Hi"), model="claude-test", max_tokens=100)
    assert [("cache_control" in block) for block in plain["system"]] == [True]


def test_the_date_line_and_the_capability_part_both_stay_out_of_the_cached_prefix() -> None:
    from anum_api.current_time import with_time_line

    line = "Current date and time: Wednesday 4 November 2026, 16:28 (Asia/Dubai, UTC+04:00). Use this for today, tomorrow, deadlines and relative dates."
    messages = build_messages([], "What can you do?", time_line=line, capabilities=caps.capability_block(all_on()))
    assert messages[0]["content"] == with_time_line(SYSTEM_PROMPT, line)
    body = build_messages_request(messages, model="claude-test", max_tokens=100)
    assert body["system"][0]["text"] == SYSTEM_PROMPT and "cache_control" in body["system"][0]
    assert body["system"][1]["text"].startswith(caps.CAPABILITY_HEADER) and "cache_control" not in body["system"][1]
    last = body["messages"][-1]["content"]
    assert last[-1]["text"] == line and "cache_control" not in last[-1]


def test_voice_messages_carry_the_spoken_list_as_a_trailing_part() -> None:
    facts = WorkspaceSnapshot(tasks_total=0, running=0, waiting_approval=0, pending_approvals=0)
    block = caps.voice_block(all_on())
    messages = voice_messages("what can you do", facts, "Anum", capabilities=block)
    assert [message["role"] for message in messages] == ["system", "system", "user"]
    assert messages[1]["content"] == block
    assert len(caps.spoken_capabilities(all_on())) < 400


def test_spoken_capabilities_in_english_and_arabic() -> None:
    found = [caps.Capability("tasks", True), caps.Capability("email", True),
             caps.Capability("act", False, reason="off: a workspace owner turns it on under Integrations > Action browser",
                             reason_ar="متوقف: يشغله مالك مساحة العمل من التكاملات > متصفح الإجراءات")]
    english = caps.spoken_capabilities(found)
    assert english.startswith("Here ANUM can handle: Tasks and agents, Email.")
    assert "Action browser is off: a workspace owner turns it on under Integrations > Action browser." in english
    arabic = caps.spoken_capabilities(found, arabic=True)
    assert "المهام والوكلاء، البريد" in arabic and "متصفح الإجراءات" in arabic


# -- routing --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "errand"),
    [
        ("book me a flight Friday", True),
        ("Please book a table for two tonight", True),
        ("Can you download my bank statement", True),
        ("go to emirates.com and check my booking", True),
        ("احجز لي رحلة يوم الجمعة", True),
        ("What is a good book to read?", False),
        ("open config.yaml", False),
        ("Explain how approvals work", False),
    ],
)
def test_errands_on_a_website_are_recognised_from_the_leading_verb(question: str, errand: bool) -> None:
    assert caps.asks_web_errand(question) is errand


def test_web_act_requests_are_routed_to_a_task_not_to_the_model() -> None:
    skills = default_skill_registry()
    tools = main.tool_registry.names
    assert chat_tools.chat_route("go to https://www.example.com and book a table for two", skills, tools) == ("action", "web.act")
    assert chat_tools.chat_route("open https://www.example.com and read it", skills, tools) == ("web", "web.browse_page")
    assert chat_tools.chat_intent("افتح https://www.example.com واحجز طاولة", skills, tools) == "action"


def test_an_action_request_with_the_action_browser_on_becomes_a_task(browser_service: None) -> None:
    switch(ACTION_INTEGRATION_ID, True)
    gateway = use(RecordingGateway())
    stream = ask("go to https://www.example.com and book a table for two on Friday")
    found = cards(stream)
    assert found and found[0]["category"] == "action" and found[0]["task_id"]
    assert found[-1]["state"] in {"running", "approval", "results", "failed"}
    task = store.tasks[found[0]["task_id"]]
    assert task.prompt.startswith("go to https://www.example.com")
    assert gateway.chats == []  # never a plain model reply


def test_an_action_request_with_the_action_browser_off_gets_the_off_card(browser_service: None) -> None:
    gateway = use(RecordingGateway())
    stream = ask("go to https://www.example.com and book a table for two on Friday")
    [card] = cards(stream)
    assert card["state"] == "unavailable" and card["category"] == "action" and card["task_id"] is None
    assert card["error_code"] == "capability_off:act"
    assert card["summary"] == (
        "Action browser is not available right now, off: a workspace owner turns it on under Integrations > Action browser. "
        "Once it is on, ANUM runs this request as a task under approvals."
    )
    assert store.tasks == {} and gateway.chats == [] and gateway.plans == []
    assert stream[-1][1]["assistant_turn"]["card"]["state"] == "unavailable"


def test_the_off_card_is_in_arabic_for_an_arabic_request() -> None:
    use(RecordingGateway())
    [card] = cards(ask("افتح https://www.example.com واحجز طاولة"))
    assert card["state"] == "unavailable"
    assert card["summary"].startswith("متصفح الإجراءات غير متاح الآن، غير مجهز على هذا الخادم")
    [english] = cards(ask("go to https://www.example.com and book a table", language="ar"))
    assert english["summary"].startswith("متصفح الإجراءات غير متاح الآن")


def test_an_errand_without_a_site_asks_for_it_when_the_action_browser_is_on(browser_service: None) -> None:
    switch(ACTION_INTEGRATION_ID, True)
    gateway = use(RecordingGateway())
    [card] = cards(ask("book me a flight Friday"))
    assert card["state"] == "needs_input" and card["category"] == "action"
    assert "https://" in card["summary"] and "action browser" in card["summary"]
    assert store.tasks == {} and gateway.chats == []
    [arabic] = cards(ask("احجز لي رحلة يوم الجمعة"))
    assert arabic["state"] == "needs_input" and arabic["summary"].startswith("يستطيع ANUM")


def test_an_errand_without_a_site_says_the_action_browser_is_off() -> None:
    gateway = use(RecordingGateway())
    [card] = cards(ask("book me a flight Friday"))
    assert card["state"] == "unavailable" and card["error_code"] == "capability_off:act"
    assert gateway.chats == []


def test_a_page_to_read_with_browser_research_off_gets_the_off_card(browser_service: None) -> None:
    use(RecordingGateway())
    [card] = cards(ask("open https://www.example.com and read it"))
    assert card["state"] == "unavailable" and card["category"] == "web"
    assert "Integrations > Browser research" in card["summary"]


def test_a_book_question_is_still_plain_conversation() -> None:
    gateway = use(RecordingGateway(reply="Try a classic."))
    stream = ask("What is a good book to read?")
    assert cards(stream) == [] and len(gateway.chats) == 1



# -- voice ----------------------------------------------------------------------------------------


def say(text: str, locale: str = "en-US") -> list[tuple[str, dict[str, Any]]]:
    session = client.post("/api/v1/voice/sessions", headers=OWNER, json={"locale": locale})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    segment = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=OWNER,
        json={"text": text, "is_final": True, "client_sequence": 0},
    ).json()
    return frames(client.post(f"/api/v1/voice/sessions/{session_id}/ask/stream", headers=OWNER, json={"transcript_segment_id": segment["id"]}))


def spoken(stream: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(data["text"] for kind, data in stream if kind == "delta")


def test_voice_says_what_anum_can_do_from_the_live_list() -> None:
    reply = spoken(say("What can you do?"))
    assert reply.startswith("Here ANUM can handle: Tasks and agents")
    assert "waits for your approval" in reply and "can't" not in reply
    arabic = spoken(say("ماذا تستطيع أن تفعل؟", locale="ar-AE"))
    assert arabic.startswith("في مساحة العمل هذه أستطيع عبر ANUM: المهام والوكلاء")


def test_voice_says_the_action_browser_is_off_instead_of_refusing(browser_service: None) -> None:
    stream = say("book me a flight Friday")
    assert [data["card"] for kind, data in stream if kind == "action"] == []
    assert spoken(stream).startswith("Action browser is not available right now, off: a workspace owner turns it on under Integrations > Action browser")
    assert store.tasks == {}


def test_voice_asks_for_the_site_when_the_action_browser_is_on(browser_service: None) -> None:
    switch(ACTION_INTEGRATION_ID, True)
    reply = spoken(say("book me a flight Friday"))
    assert reply.startswith("ANUM can do this with the action browser") and "Assistant chat" in reply
    assert store.tasks == {}


def test_voice_routes_a_website_task_to_a_governed_task_when_the_action_browser_is_on(browser_service: None) -> None:
    switch(ACTION_INTEGRATION_ID, True)
    stream = say("go to https://www.example.com and book a table for two")
    action = [data["card"] for kind, data in stream if kind == "action"]
    assert action, stream
    assert len(store.tasks) == 1 and next(iter(store.tasks.values())).prompt.startswith("go to https://www.example.com")


def test_a_voice_model_answer_carries_the_spoken_list() -> None:
    from anum_api.voice import prepare_ask_in_own_session
    from anum_api.voice import VoiceAskCreate

    session = client.post("/api/v1/voice/sessions", headers=OWNER, json={"locale": "en-US"}).json()
    segment = client.post(
        f"/api/v1/voice/sessions/{session['id']}/transcript", headers=OWNER,
        json={"text": "How should I plan my week?", "is_final": True, "client_sequence": 0},
    ).json()
    prepared = prepare_ask_in_own_session(session["id"], VoiceAskCreate(transcript_segment_id=segment["id"]), CONTEXT)
    assert prepared.reply is None and prepared.capabilities.startswith(caps.CAPABILITY_HEADER)
    assert "Here ANUM can handle" in prepared.capabilities
