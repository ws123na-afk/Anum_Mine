"""A refused, empty or cut-off model answer is never stored as a finished task result
(docs/model-gateway.md#finish-reasons)."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from anum_api.agent_tools import (
    RESPOND_CUT_OFF_NOTE,
    RESPOND_EMPTY_AT_LIMIT_TEXT,
    RESPOND_REFUSED_TEXT,
)
from anum_api.anthropic_gateway import AnthropicGateway
from anum_api.model_gateway import (
    ModelRoute,
    answer_problem,
    build_default_model_gateway,
    build_model_gateway,
)
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import Task, TaskStatus, TenantContext, utc_now
from anum_api.settings import Settings
from anum_api.store import InMemoryStore

PROMPT = "Compare the three lease options and recommend one"
CUT_TEXT = "Dear Mr Khan, the three options for the lease are: first,"
THINKING_ONLY = [{"type": "thinking", "thinking": "", "signature": "x"}]


def _task() -> Task:
    now = utc_now()
    return Task(id="task_finish", tenant_id="tenant_f", workspace_id="workspace_f", title="Lease", prompt=PROMPT,
                status=TaskStatus.CREATED, created_at=now, updated_at=now, created_by="user_f")


def _context() -> TenantContext:
    return TenantContext(tenant_id="tenant_f", workspace_id="workspace_f", user_id="user_f", roles=["owner"])


def _anthropic(content: list[dict], stop_reason: str, sent: list[dict] | None = None) -> AnthropicGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "model": "claude-sonnet-5-5", "content": content, "stop_reason": stop_reason,
            "usage": {"input_tokens": 50, "output_tokens": 1024},
        })

    return AnthropicGateway(api_key="example", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _run(gateway) -> object:
    runtime = AgentRuntime(gateway, InMemoryRepository(InMemoryStore()))
    run, approval = asyncio.run(runtime.run_task(_task(), _context()))
    assert approval is None
    return run


def test_thinking_that_used_the_whole_output_limit_fails_the_task() -> None:
    run = _run(_anthropic(THINKING_ONLY, "max_tokens"))
    assert run.status == TaskStatus.FAILED
    assert run.result == RESPOND_EMPTY_AT_LIMIT_TEXT
    assert "Prepared an internal ANUM response" not in str(run.result)


def test_a_refusal_fails_the_task_with_a_plain_reason() -> None:
    run = _run(_anthropic(THINKING_ONLY, "refusal"))
    assert run.status == TaskStatus.FAILED
    assert run.result == RESPOND_REFUSED_TEXT


def test_a_refusal_with_partial_text_still_fails() -> None:
    run = _run(_anthropic([{"type": "text", "text": "I can't help with"}], "refusal"))
    assert run.status == TaskStatus.FAILED
    assert run.result == RESPOND_REFUSED_TEXT


def test_a_cut_off_answer_says_it_is_not_complete() -> None:
    run = _run(_anthropic([{"type": "text", "text": CUT_TEXT}], "max_tokens"))
    assert run.status == TaskStatus.COMPLETED
    assert str(run.result).startswith(CUT_TEXT)
    assert str(run.result).endswith(RESPOND_CUT_OFF_NOTE)


def test_a_finished_answer_is_unchanged() -> None:
    run = _run(_anthropic([{"type": "text", "text": "Take option two."}], "end_turn"))
    assert run.status == TaskStatus.COMPLETED
    assert run.result == "Take option two."


@pytest.mark.parametrize(("finish", "content", "status"), [
    ("length", "", TaskStatus.FAILED),
    ("content_filter", "", TaskStatus.FAILED),
    ("length", "Half an ans", TaskStatus.COMPLETED),
])
def test_openai_compatible_finish_reasons_are_read_the_same_way(finish: str, content: str, status: TaskStatus) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "llama3.2", "choices": [{"message": {"content": content}, "finish_reason": finish}]})

    gateway = build_model_gateway("ollama", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run = _run(gateway)
    assert run.status == status
    if status == TaskStatus.COMPLETED:
        assert RESPOND_CUT_OFF_NOTE in str(run.result)


def test_answer_problem_names_each_case() -> None:
    async def one(content: list[dict], stop: str) -> str | None:
        return answer_problem(await _anthropic(content, stop).generate_text("hi"))

    assert asyncio.run(one(THINKING_ONLY, "max_tokens")) == "empty_at_limit"
    assert asyncio.run(one([{"type": "text", "text": "x"}], "max_tokens")) == "cut_off"
    assert asyncio.run(one([{"type": "text", "text": "x"}], "refusal")) == "refused"
    assert asyncio.run(one([{"type": "text", "text": "x"}], "end_turn")) is None


def test_model_call_log_line_carries_the_finish_reason(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging.getLogger("anum.model_gateway"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="anum.model_gateway")
    asyncio.run(_anthropic(THINKING_ONLY, "max_tokens").generate_text("hi"))
    record = next(r for r in caplog.records if r.getMessage().startswith("model_call "))
    assert record.anum_model_call["finish_reason"] == "max_tokens"  # type: ignore[attr-defined]
    assert "finish_reason=max_tokens" in record.getMessage()


def test_anthropic_defaults_leave_room_for_thinking_and_set_the_capable_effort() -> None:
    config = Settings(environment="test", model_provider="anthropic", model_api_key="example")
    assert config.model_capable_effort == "medium"
    assert config.model_anthropic_max_output_tokens == 4096
    assert config.model_max_output_tokens == 1024
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "m", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})

    gateway = build_default_model_gateway(config)
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    asyncio.run(gateway.for_route(ModelRoute.CAPABLE).generate_text("plan"))  # type: ignore[attr-defined]
    assert sent[0]["max_tokens"] == 4096
    assert sent[0]["output_config"] == {"effort": "medium"}


def test_the_anthropic_output_cap_is_configurable() -> None:
    gateway = AnthropicGateway(api_key="example", max_output_tokens=2000)
    assert gateway.max_output_tokens == 2000
