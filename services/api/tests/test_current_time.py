"""The current date and time line in model prompts (docs/model-gateway.md#current-date-and-time).

The owner asked AI Chat "What day is it today?" and the model answered that it could not see
a clock. Every prompt ANUM sends for a user now carries the date and time in the user's zone,
from one injectable clock, and the Anthropic adapter keeps it out of the cached prefix.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api import current_time
from anum_api.anthropic_gateway import build_messages_request, count_cache_breakpoints
from anum_api.current_time import (
    format_time_line,
    frozen,
    is_time_line,
    resolve_zone,
    split_time_line,
    time_line_for,
    with_time_line,
)
from anum_api.model_gateway import MockModelGateway, ModelCallMetadata, ModelResponse, ModelUsage
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings
from anum_api.team_models import MemberProfile
from anum_api.team_store import memory_team_store

# Wednesday 4 November 2026, 10:32 UTC = 14:32 in Dubai (UTC+04:00).
AT = datetime(2026, 11, 4, 10, 32, 5, tzinfo=UTC)
EN_LINE = (
    "Current date and time: Wednesday 4 November 2026, 14:32 (Asia/Dubai, UTC+04:00). "
    "Use this for today, tomorrow, deadlines and relative dates."
)
AR_LINE = (
    "التاريخ والوقت الآن: الأربعاء 4 نوفمبر 2026، 14:32 (Asia/Dubai، UTC+04:00). "
    "استخدم هذا لليوم والغد والمواعيد النهائية والتواريخ النسبية."
)
TENANT, WORKSPACE, USER = "tenant_clock", "workspace_clock", "user_clock"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=USER, roles=["member"])
H = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": USER, "x-user-roles": "member"}


@pytest.fixture(autouse=True)
def _memory_and_frozen_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "default_time_zone", "Asia/Dubai")
    memory_team_store._ws(CONTEXT).profiles.pop(USER, None)
    with frozen(AT):
        yield
    memory_team_store._ws(CONTEXT).profiles.pop(USER, None)


def _profile(zone: str) -> None:
    memory_team_store.save_profile(CONTEXT, MemberProfile(user_id=USER, display_name="Clock User", timezone=zone))


# --------------------------------------------------------------------------- the line


def test_the_line_names_weekday_date_time_zone_and_offset_in_english_and_arabic() -> None:
    assert format_time_line(current_time.now(), "Asia/Dubai", "en") == EN_LINE
    assert format_time_line(current_time.now(), "Asia/Dubai", "ar") == AR_LINE
    # Another zone moves the day and the offset with it (negative offsets too).
    assert format_time_line(AT, "America/New_York").startswith(
        "Current date and time: Wednesday 4 November 2026, 05:32 (America/New_York, UTC-05:00)."
    )
    assert "(Asia/Kolkata, UTC+05:30)" in format_time_line(AT, "Asia/Kolkata")
    assert format_time_line(datetime(2026, 11, 4, 22, 0, tzinfo=UTC), "Asia/Dubai").startswith(
        "Current date and time: Thursday 5 November 2026, 02:00"
    )
    for line in (EN_LINE, AR_LINE):
        assert "–" not in line and "—" not in line and is_time_line(line)


def test_the_clock_is_injectable_and_always_aware_utc() -> None:
    assert current_time.now() == AT
    with frozen(datetime(2027, 1, 1, 0, 0)):  # noqa: DTZ001 (a naive value is read as UTC)
        assert current_time.now() == datetime(2027, 1, 1, tzinfo=UTC)
    assert current_time.now() == AT
    previous = current_time.set_clock(None)
    try:
        assert abs((current_time.now() - datetime.now(UTC)).total_seconds()) < 5
    finally:
        current_time.set_clock(previous)


# --------------------------------------------------------------------------- the zone


def test_zone_order_is_user_profile_then_workspace_then_setting_then_dubai(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_zone("Europe/London", "Asia/Riyadh") == "Europe/London"
    assert resolve_zone(None, "Asia/Riyadh") == "Asia/Riyadh"
    # UTC is the old profile form default, not a choice; an unknown name is skipped.
    assert resolve_zone("UTC", "Asia/Riyadh") == "Asia/Riyadh"
    assert resolve_zone("Mars/Olympus", "Not/AZone") == "Asia/Dubai"
    monkeypatch.setattr(settings, "default_time_zone", "Asia/Karachi")
    assert resolve_zone(None, None) == "Asia/Karachi"
    monkeypatch.setattr(settings, "default_time_zone", "Nowhere/Here")
    assert resolve_zone(None, None) == "Asia/Dubai"


def test_time_line_for_reads_the_users_profile_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    assert time_line_for(CONTEXT) == EN_LINE  # no profile: the default
    _profile("Asia/Riyadh")
    assert "13:32 (Asia/Riyadh, UTC+03:00)" in time_line_for(CONTEXT)
    assert time_line_for(CONTEXT, "ar").startswith("التاريخ والوقت الآن: الأربعاء 4 نوفمبر 2026، 13:32 (Asia/Riyadh،")
    _profile("UTC")
    assert time_line_for(CONTEXT) == EN_LINE


def test_a_failed_zone_lookup_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(context: TenantContext) -> dict[str, MemberProfile]:
        raise RuntimeError("database down")

    monkeypatch.setattr(memory_team_store, "list_profiles", broken)
    assert time_line_for(CONTEXT) == EN_LINE


def test_postgres_reads_the_profile_then_the_first_office_building(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, dict[str, str]]] = []

    class Result:
        def __init__(self, value: str | None) -> None:
            self.value = value

        def scalar(self) -> str | None:
            return self.value

    class Session:
        def execute(self, statement: Any, params: dict[str, str] | None = None) -> Result:
            sql = str(statement)
            executed.append((sql, params or {}))
            if "team_member_profiles" in sql:
                return Result(None)
            if "office_buildings" in sql:
                return Result("Asia/Muscat")
            return Result(None)

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            executed.append(("closed", {}))

    import anum_api.db.session as db_session

    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(db_session, "SessionLocal", Session)
    monkeypatch.setattr(db_session, "set_tenant_context", lambda *args, **kwargs: None)
    assert "(Asia/Muscat, UTC+04:00)" in time_line_for(CONTEXT)
    queries = [sql for sql, _ in executed]
    assert "team_member_profiles" in queries[0] and "office_buildings" in queries[1] and queries[-1] == "closed"
    assert executed[0][1] == {"t": TENANT, "w": WORKSPACE, "u": USER}


# --------------------------------------------------------------------------- caching


def test_with_and_split_time_line_round_trip() -> None:
    assert with_time_line("Rules.", None) == "Rules."
    assert split_time_line(with_time_line("Rules.", EN_LINE)) == ("Rules.", EN_LINE)
    assert split_time_line(EN_LINE) == ("", EN_LINE)
    assert split_time_line("Rules.\n\nMore rules.") == ("Rules.\n\nMore rules.", None)
    # Only the last paragraph counts: a line quoted earlier stays where it is.
    quoted = f"Rules.\n\n{EN_LINE}\n\nMore rules."
    assert split_time_line(quoted) == (quoted, None)


def _chat(line: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": with_time_line("You are Anum. " * 20, line)},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "What day is it today?"},
    ]


def test_anthropic_keeps_the_line_after_the_cached_system_and_conversation() -> None:
    later = format_time_line(datetime(2026, 11, 4, 10, 47, tzinfo=UTC), "Asia/Dubai")
    first = build_messages_request(_chat(EN_LINE), model="claude-haiku-5-5", max_tokens=400)
    second = build_messages_request(_chat(later), model="claude-haiku-5-5", max_tokens=400)

    # The system breakpoint sits on the static text, without the line.
    assert first["system"] == [{"type": "text", "text": ("You are Anum. " * 20), "cache_control": {"type": "ephemeral"}}]
    # The last user message: the question with its breakpoint, then the line, uncached.
    assert first["messages"][-1]["content"] == [
        {"type": "text", "text": "What day is it today?", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": EN_LINE},
    ]
    assert count_cache_breakpoints(first) == 2
    # Fifteen minutes later everything up to the last breakpoint is identical: a cache read.
    assert first["system"] == second["system"]
    assert first["messages"][:-1] == second["messages"][:-1]
    assert first["messages"][-1]["content"][0] == second["messages"][-1]["content"][0]
    assert second["messages"][-1]["content"][1]["text"] == later


def test_anthropic_one_off_and_uncached_calls_keep_the_line_out_of_the_breakpoint() -> None:
    messages = [{"role": "system", "content": with_time_line("Stable instructions.", EN_LINE)},
                {"role": "user", "content": "Task: plan the week"}]
    planning = build_messages_request(messages, model="claude-sonnet-5-5", max_tokens=400, cache_messages=False)
    assert planning["system"] == [
        {"type": "text", "text": "Stable instructions.", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": EN_LINE},
    ]
    assert planning["messages"] == [{"role": "user", "content": "Task: plan the week"}]
    assert count_cache_breakpoints(planning) == 1
    # Not cached at all (complete_chat): the system text is sent as written.
    plain = build_messages_request(messages, model="claude-sonnet-5-5", max_tokens=400, cache=False)
    assert plain["system"] == [{"type": "text", "text": f"Stable instructions.\n\n{EN_LINE}"}]
    # A system prompt that is only the line still caches nothing that changes.
    only = build_messages_request([{"role": "system", "content": EN_LINE}, {"role": "user", "content": "Hi"}],
                                  model="claude-haiku-5-5", max_tokens=100)
    assert "system" not in only and only["messages"][-1]["content"][-1] == {"type": "text", "text": EN_LINE}


def test_without_a_line_the_request_is_unchanged() -> None:
    messages = [{"role": "system", "content": "Rules."}, {"role": "user", "content": "Hi"}]
    body = build_messages_request(messages, model="claude-haiku-5-5", max_tokens=100)
    assert body["system"] == [{"type": "text", "text": "Rules.", "cache_control": {"type": "ephemeral"}}]
    assert body["messages"][-1]["content"] == [{"type": "text", "text": "Hi", "cache_control": {"type": "ephemeral"}}]


# --------------------------------------------------------------------------- AI Chat


class RecordingChat:
    provider = "test"

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        yield "It is Wednesday."


def _chat_client() -> tuple[TestClient, FastAPI]:
    from anum_api.conversations import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), app


@pytest.mark.parametrize(("language", "line"), [("en", EN_LINE), ("ar", AR_LINE)])
def test_ai_chat_tells_the_model_today_in_the_users_language(language: str, line: str) -> None:
    from anum_api.conversations import conversation_store
    from anum_api.voice import voice_model_gateway

    conversation_store.clear()
    client, app = _chat_client()
    model = RecordingChat()
    app.dependency_overrides[voice_model_gateway] = lambda: model
    conversation = client.post("/api/v1/conversations", headers=H, json={}).json()["id"]
    response = client.post(
        f"/api/v1/conversations/{conversation}/ask",
        headers={**H, "accept-language": language},
        json={"content": "What day is it today?"},
    )
    assert response.status_code == 200
    system = model.messages[0][0]
    assert system["role"] == "system" and system["content"].endswith(f"\n\n{line}")
    assert model.messages[0][-1] == {"role": "user", "content": "What day is it today?"}
    conversation_store.clear()


def test_ai_chat_build_messages_puts_the_line_last_after_untrusted_blocks() -> None:
    from anum_api.conversations import build_messages

    messages = build_messages([], "When is the deadline?", memory_blocks=["<<untrusted-data id=1>>x<<end>>"], time_line=EN_LINE)
    assert split_time_line(messages[0]["content"])[1] == EN_LINE
    assert build_messages([], "Hi")[0]["content"] == split_time_line(build_messages([], "Hi", time_line=EN_LINE)[0]["content"])[0]


def test_the_answer_above_a_tool_card_carries_the_line() -> None:
    from anum_api.agent_tools import ToolCall, ToolResult
    from anum_api.chat_tools import summary_messages

    call = ToolCall(name="search_email", arguments={"query": "invoices"})
    result = ToolResult(tool_name="search_email", status="succeeded", summary="No invoices found.", output={"messages": []})
    messages = summary_messages("Any invoices due this week?", call, result, time_line=AR_LINE)
    assert split_time_line(messages[0]["content"])[1] == AR_LINE
    assert messages[1] == {"role": "user", "content": "Any invoices due this week?"}


# --------------------------------------------------------------------------- voice


class SpeakingModel:
    provider = "openai-compatible"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        yield self.reply

    async def complete_chat(self, messages, *, max_tokens=None) -> ModelResponse:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        return ModelResponse(
            text=self.reply,
            usage=ModelUsage(input_tokens=1, output_tokens=1, provider="test", model="test", estimated_cost_usd=0),
            metadata=ModelCallMetadata(latency_ms=0, finish_reason="stop"),
        )


@pytest.mark.parametrize(("locale", "line"), [("en-US", EN_LINE), ("ar-AE", AR_LINE)])
def test_voice_answers_know_the_date_and_may_say_it(locale: str, line: str) -> None:
    from anum_api.voice import router, voice_model_gateway, voice_store

    voice_store.clear()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    model = SpeakingModel("Today is Wednesday 4 November 2026.")
    app.dependency_overrides[voice_model_gateway] = lambda: model
    session = client.post("/api/v1/voice/sessions", headers=H, json={"locale": locale}).json()["id"]
    # A strict question (it asks for tasks): every figure must be in this turn's evidence.
    segment = client.post(f"/api/v1/voice/sessions/{session}/transcript", headers=H,
                          json={"text": "What tasks are due today?", "client_sequence": 0}).json()["id"]
    answer = client.post(f"/api/v1/voice/sessions/{session}/ask", headers=H, json={"transcript_segment_id": segment}).json()
    assert answer["reply"] == "Today is Wednesday 4 November 2026."
    assert model.messages[0][0]["content"].endswith(f"\n\n{line}")
    voice_store.clear()


def test_voice_messages_and_the_figure_guard_take_the_line() -> None:
    from anum_api.voice_assistant import WorkspaceSnapshot, voice_messages
    from anum_api.voice_figures import FigureGuard

    facts = WorkspaceSnapshot(tasks_total=0, running=0, waiting_approval=0, pending_approvals=0)
    messages = voice_messages("What is the date?", facts, "Anum", time_line=EN_LINE)
    assert split_time_line(messages[0]["content"])[1] == EN_LINE
    guard = FigureGuard("How many tasks are due today?", arabic=False, evidence=[EN_LINE])
    assert guard.unsupported("Today is 4 November 2026, 14:32.") == []
    assert guard.unsupported("You have 9 tasks due.") == [9.0]


# --------------------------------------------------------------------------- planning


class RecordingPlanner(MockModelGateway):
    provider = "test"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        return await super().generate_text(prompt)


def _task(prompt: str):  # type: ignore[no-untyped-def]
    from anum_api.schemas import Task, TaskStatus

    now = utc_now()
    return Task(id="task_clock", tenant_id=TENANT, workspace_id=WORKSPACE, created_by=USER, title="Plan",
                prompt=prompt, status=TaskStatus.QUEUED, created_at=now, updated_at=now)


def test_the_task_planner_gets_the_line_after_the_task() -> None:
    from anum_api.agent_planning import AgentPlanner
    from anum_api.agent_skills import default_skill_registry
    from anum_api.agent_tools import default_tool_registry

    gateway = RecordingPlanner()
    planner = AgentPlanner(gateway, default_skill_registry(), default_tool_registry())
    asyncio.run(planner.plan(_task("Book the review for next Friday"), time_line=EN_LINE))
    assert gateway.prompts[0] == f"Book the review for next Friday\n\n{EN_LINE}"


@pytest.mark.parametrize(("prompt", "line"), [("Book the review for next Friday", EN_LINE), ("احجز المراجعة يوم الجمعة القادم", AR_LINE)])
def test_the_runtime_plans_with_the_users_date_line(prompt: str, line: str) -> None:
    from anum_api.dependencies import memory_repository
    from anum_api.runtime import AgentRuntime
    from anum_api.schemas import AgentRun

    gateway = RecordingPlanner()
    runtime = AgentRuntime(gateway, memory_repository)
    task = _task(prompt)
    run = AgentRun(id="run_clock", task_id=task.id, tenant_id=TENANT, workspace_id=WORKSPACE, status=task.status,
                   created_at=task.created_at, updated_at=task.created_at)
    asyncio.run(runtime.plan_with_model(task, run, CONTEXT, offload=True))
    assert gateway.prompts[0].endswith(f"\n\n{line}")


def test_the_mock_planner_gets_no_line() -> None:
    from anum_api.dependencies import memory_repository
    from anum_api.runtime import AgentRuntime
    from anum_api.schemas import AgentRun

    runtime = AgentRuntime(MockModelGateway(), memory_repository)
    task = _task("Book the review")
    run = AgentRun(id="run_mock", task_id=task.id, tenant_id=TENANT, workspace_id=WORKSPACE, status=task.status,
                   created_at=task.created_at, updated_at=task.created_at)
    planned, _ = asyncio.run(runtime.plan_with_model(task, run, CONTEXT, offload=True))
    assert planned.model_response.text == "Prepared ANUM plan for: Book the review"


# --------------------------------------------------------------------------- knowledge base


class RecordingAnswers(MockModelGateway):
    provider = "test"

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    async def complete_chat(self, messages, *, max_tokens=None) -> ModelResponse:  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        return ModelResponse(
            text="It expires this month [1].",
            usage=ModelUsage(input_tokens=1, output_tokens=1, provider="test", model="test", estimated_cost_usd=0),
            metadata=ModelCallMetadata(latency_ms=0, finish_reason="stop"),
        )


def test_knowledge_base_answers_get_the_line() -> None:
    from anum_api.knowledge_base import knowledge_store
    from anum_api.main import app
    from anum_api.retrieval import memory_retrieval_store
    from anum_api.voice import voice_model_gateway

    knowledge_store.clear()
    memory_retrieval_store.clear()
    client = TestClient(app)
    model = RecordingAnswers()
    app.dependency_overrides[voice_model_gateway] = lambda: model
    try:
        collection = client.post("/api/v1/knowledge/collections", headers=H, json={"name": "Licences"}).json()
        note = client.post(f"/api/v1/knowledge/collections/{collection['id']}/notes", headers=H,
                           json={"title": "Trade licence", "content": "The trade licence expires on 30 November 2026."})
        assert note.status_code == 201, note.text
        answer = client.post("/api/v1/knowledge/ask", headers=H, json={"question": "When does the trade licence expire?"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["status"] == "answered"
        assert split_time_line(model.messages[0][0]["content"])[1] == EN_LINE
    finally:
        app.dependency_overrides.clear()
        knowledge_store.clear()
        memory_retrieval_store.clear()


def test_the_setting_is_documented_for_every_deployment() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    assert type(settings).model_fields["default_time_zone"].default == "Asia/Dubai"
    for path in ("infra/oracle/.env.example", "services/api/.env.example"):
        assert "ANUM_DEFAULT_TIME_ZONE=Asia/Dubai" in (root / path).read_text(encoding="utf-8")
    assert "ANUM_DEFAULT_TIME_ZONE: Asia/Dubai" in (root / "infra/helm/anum/values.yaml").read_text(encoding="utf-8")
    assert "ANUM_DEFAULT_TIME_ZONE: ${ANUM_DEFAULT_TIME_ZONE:-Asia/Dubai}" in (root / "infra/oracle/compose.yaml").read_text(encoding="utf-8")
    assert "#current-date-and-time" in json.dumps((root / "docs/model-gateway.md").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- drafting paths


def _response(text: str = "{}") -> ModelResponse:
    return ModelResponse(
        text=text,
        usage=ModelUsage(input_tokens=1, output_tokens=1, provider="test", model="test", estimated_cost_usd=0),
        metadata=ModelCallMetadata(latency_ms=0, finish_reason="stop"),
    )


class Drafter:
    """A real-looking provider that records every prompt (plain and cached-instruction calls)."""

    provider = "test"
    model = "test"

    def __init__(self, cached: bool = False) -> None:
        self.prompts: list[str] = []
        self.instructions: list[str] = []
        if cached:
            self.generate_with_instructions = self._with_instructions

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.prompts.append(prompt)
        return _response()

    async def _with_instructions(self, instructions: str, prompt: str) -> ModelResponse:
        self.instructions.append(instructions)
        self.prompts.append(prompt)
        return _response()


@pytest.mark.parametrize(("goal", "line"), [("Book Friday morning at the clinic", EN_LINE), ("احجز موعدا صباح الجمعة", AR_LINE)])
def test_the_action_browser_plans_each_step_with_the_date_line(goal: str, line: str) -> None:
    from anum_api.action_browser.planner import INSTRUCTIONS, ModelActionPlanner

    model = Drafter()
    planner = ModelActionPlanner(lambda: model, lambda language: time_line_for(CONTEXT, language))
    snapshot = {"url": "https://clinic.example/book", "title": "Book", "elements": [], "text": "Pick a day"}
    asyncio.run(planner.next_step(goal=goal, scope={"sites": ["clinic.example"]}, history=[], snapshot=snapshot))
    head, found = split_time_line(model.prompts[0])
    assert found == line and head.startswith(INSTRUCTIONS) and f"Goal (the owner's own words): {goal}" in head
    # Without a clock the prompt is unchanged (fakes and older callers).
    plain = Drafter()
    asyncio.run(ModelActionPlanner(lambda: plain).next_step(goal=goal, scope={"sites": ["clinic.example"]}, history=[], snapshot=snapshot))
    assert split_time_line(plain.prompts[0])[1] is None and plain.prompts[0].startswith(INSTRUCTIONS)


def test_the_default_action_planner_reads_the_owners_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import onboarding
    from anum_api.action_browser.tool import default_planner_factory

    model = Drafter()
    monkeypatch.setattr(onboarding, "budgeted_model_gateway", lambda context, gateway: model)
    _profile("Asia/Riyadh")
    planner = default_planner_factory(object())(CONTEXT)
    asyncio.run(planner.next_step(goal="Book Friday morning", scope={}, history=[], snapshot={}))
    assert model.prompts[0].endswith("13:32 (Asia/Riyadh, UTC+03:00). Use this for today, tomorrow, deadlines and relative dates.")


def test_mailbox_ai_prompts_end_with_the_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import email_inbox
    from anum_api.mailbox import ai as mail_ai

    model = Drafter()
    monkeypatch.setattr(mail_ai, "ai_unavailable_reason", lambda context: None)
    monkeypatch.setattr(email_inbox, "_gateway_for", lambda context: model)
    asyncio.run(mail_ai._ask_model(CONTEXT, "Summarise this thread."))
    assert model.prompts == [f"Summarise this thread.\n\n{EN_LINE}"]


def test_email_triage_and_reply_drafts_know_today(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from anum_api import email_inbox

    model = Drafter()
    monkeypatch.setattr(email_inbox, "_gateway_for", lambda context: model)
    monkeypatch.setattr(email_inbox, "email_block", lambda message, text, truncated: "EMAIL BLOCK")
    monkeypatch.setattr(email_inbox, "rules_triage", lambda message: (email_inbox.Category("other"), email_inbox.Priority("normal")))
    message = SimpleNamespace(snippet="Can you deliver?")
    body = SimpleNamespace(text="Can you deliver next week?", truncated=False)
    asyncio.run(email_inbox.triage_with_model(CONTEXT, message, body))  # type: ignore[arg-type]
    asyncio.run(email_inbox.model_reply(CONTEXT, message, body, "قل إننا سنسلم يوم الثلاثاء القادم"))  # type: ignore[arg-type]
    assert split_time_line(model.prompts[0])[1] == EN_LINE
    assert split_time_line(model.prompts[1])[1] == AR_LINE


def test_business_assist_puts_the_line_in_the_request_not_the_cached_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.business import assist

    cached = Drafter(cached=True)
    monkeypatch.setattr(assist, "workspace_gateway", lambda context: cached)
    asyncio.run(assist._ask(CONTEXT, "Write a follow up.", "Offer a call next Tuesday.", []))
    assert cached.instructions == ["Write a follow up."]
    assert cached.prompts == [f"Offer a call next Tuesday.\n\n{EN_LINE}"]
    plain = Drafter()
    monkeypatch.setattr(assist, "workspace_gateway", lambda context: plain)
    asyncio.run(assist._ask(CONTEXT, "Write a follow up.", "Offer a call next Tuesday.", []))
    assert plain.prompts == [f"Write a follow up.\n\nOffer a call next Tuesday.\n\n{EN_LINE}"]


def test_marketing_ideas_get_the_line_after_the_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.marketing import assist
    from anum_api.marketing.models import AssistIdeas

    cached = Drafter(cached=True)
    monkeypatch.setattr(assist, "workspace_gateway", lambda context: cached)
    asyncio.run(assist.ideas(CONTEXT, AssistIdeas(brief="Launch next Monday"), brand_voice="", default_tags={}, tz="Asia/Dubai"))
    assert cached.prompts and split_time_line(cached.prompts[0])[1] == EN_LINE
    assert all(not is_time_line(part) for text in cached.instructions for part in text.split("\n\n"))


@pytest.mark.parametrize(("language", "line"), [("en", EN_LINE), ("ar", AR_LINE)])
def test_brevo_compose_gets_the_line_in_the_language_of_the_email(monkeypatch: pytest.MonkeyPatch, language: str, line: str) -> None:
    from anum_api.brevo import compose
    from anum_api.marketing import assist

    cached = Drafter(cached=True)
    monkeypatch.setattr(assist, "workspace_gateway", lambda context: cached)
    asyncio.run(compose._model_sections(CONTEXT, subject="Sale", outline=["Offer"], brief="Sale ends Friday", brand="", voice="",
                                        language=language))
    assert split_time_line(cached.prompts[0])[1] == line
    assert cached.instructions == [compose.INSTRUCTIONS]


def test_finance_commentary_and_research_summaries_know_today(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import onboarding
    from anum_api.finance import research_summary
    from anum_api.finance.intel import commentary

    model = Drafter()
    monkeypatch.setattr(onboarding, "budgeted_model_gateway", lambda context, gateway: model)
    commentary._ask(CONTEXT, "Comment on this month.")
    research_summary.ask(CONTEXT, "Summarise the research.")
    assert model.prompts == [f"Comment on this month.\n\n{EN_LINE}", f"Summarise the research.\n\n{EN_LINE}"]
